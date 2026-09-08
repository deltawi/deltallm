"""Isolated selector regression profile; fake billing/Redis, deterministic provider.

This measures logical dependency calls, not PostgreSQL durability or network latency.
Run: python -m tests.performance.selector_prerequisites_profile --output-dir <directory>
"""

import argparse
import asyncio
from collections import Counter
import json
import logging
from pathlib import Path

import httpx

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from tests.conftest import FakeRedis
from tests.performance.routing_cache_profile import queue_slope
from tests.router.selection.provider_fixtures import bridge, response_body
from tests.test_operation_reservation import make_operation
from src.billing.operation_reservation import ComponentState, ReservedOperation
from src.cache.execution_eligibility import ResponseCacheEligibility, ResponseCacheOutcome
from src.models.requests import ChatCompletionRequest
from src.route_policy_contract import LLMTierSelectorPolicy, SelectorLane
from src.router.execution import RequestDeadline
from src.router.health_state import DeploymentHealthRef
from src.router.selection.capacity import SelectorCapacityBounds
from src.router.selection.contracts import SelectorPolicyIdentity
from src.router.selection.prerequisites import admitted_selector_service
from src.router.selection.request_state import RequestSelectorState
from src.router.state import RedisStateBackend


class ObservedBilling:
    def __init__(self):
        self.calls = Counter()

    async def reserve(self, operation, **kwargs):
        self.calls["reserve"] += 1
        return ReservedOperation(
            operation=operation,
            selector_state=ComponentState.RESERVED,
            answer_state=ComponentState.RESERVED,
        )

    async def dispatch(self, operation, **kwargs):
        self.calls["dispatch"] += 1

    async def accept_selector(self, operation, charge, **kwargs):
        self.calls["accept"] += 1
        assert charge.attribution == operation.attribution
        assert charge.customer_charge == charge.provider_cost

    async def unattempted(self, operation, **kwargs):
        raise AssertionError("profile unexpectedly defaulted without a provider call")

    async def confirm_not_dispatched(self, operation, **kwargs):
        raise AssertionError("profile unexpectedly defaulted before dispatch")


async def main(output_dir):
    logging.getLogger("httpx").setLevel(logging.WARNING)
    provider_calls = 0

    async def provider_response(request):
        nonlocal provider_calls
        provider_calls += 1
        await asyncio.sleep(0.001)  # Explicit fixed mock provider latency, not a test workaround.
        return httpx.Response(200, json=response_body())

    billing = ObservedBilling()
    redis = FakeRedis()
    capacity = RedisStateBackend(redis)
    policy = LLMTierSelectorPolicy(
        kind="llm-tier",
        classifier_deployment_id="classifier-concrete",
        lanes=(
            SelectorLane(id="economy", rank=0, description="Routine work"),
            SelectorLane(id="quality", rank=1, description="Complex work"),
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider_response)) as client:
        provider = bridge(client)

        async def request(index, request_id):
            operation = make_operation()
            operation = operation.model_copy(
                update={
                    "attribution": operation.attribution.model_copy(
                        update={"deployment_id": "classifier-concrete"}
                    ),
                    "selector_ceiling": operation.selector_ceiling.model_copy(
                        update={"deployment_id": "classifier-concrete"}
                    ),
                }
            )
            service = admitted_selector_service(
                provider=provider,
                capacity_owner=capacity,
                billing=billing,
                operation=operation,
                cache=ResponseCacheEligibility(ResponseCacheOutcome.MISS),
                capacity=SelectorCapacityBounds(
                    DeploymentHealthRef("classifier-concrete"),
                    rpm=10000,
                    tpm=100000000,
                    concurrency=16,
                    token_allowance=1064,
                ),
            )
            result = await service.select_once(
                state=RequestSelectorState(RequestDeadline.after(2)),
                payload=ChatCompletionRequest(
                    model="group", messages=[{"role": "user", "content": "Hello"}]
                ),
                token_estimate=10,
                policy=policy,
                identity=SelectorPolicyIdentity(fingerprint="route-policy-v1:" + "a" * 64),
            )
            assert not result.used_default
            return RequestResult(status_code=200, bytes_received=0)

        run = await run_constant_arrival(
            rate=10, duration_seconds=20, max_in_flight=16, request=request
        )
    report = summarize(run, target_rate=10)
    report.update(
        environment="isolated selector; fake billing/Redis; 1ms provider mock",
        billing_operations=dict(billing.calls),
        provider_calls=provider_calls,
        ttft_note="selector contribution only; real streaming integration remains PR 4",
        **queue_slope(run, 20),
    )
    write_results(run, report, output_dir)
    print(json.dumps(report), flush=True)
    assert report["success_count"] == 200 and not report["generator_dropped_count"]
    assert billing.calls == {"reserve": 200, "dispatch": 200, "accept": 200}
    assert provider_calls == 200


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output_dir))
