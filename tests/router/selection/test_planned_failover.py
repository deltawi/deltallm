import pytest

from src.models.errors import RoutingFailureAction, ServiceUnavailableError
from src.router import CooldownManager, FallbackConfig, FailoverManager
from src.router.candidates import invalidate_candidate_plan_cache
from tests.router.selection.test_planning import decide, group, ids, plan, routing


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["economy", "quality"])
@pytest.mark.parametrize("retry_count", [0, 1, 2])
async def test_failover_consumes_lane_plan_without_reclassification(
    selector_policy, policy_identity, lane, retry_count
):
    router, backend = routing([group(selector_policy)])
    context = {"routing_mode": "chat"}
    operation, provider = await decide(
        context, selector_policy, policy_identity, f'{{"lane":"{lane}"}}'
    )
    candidate_plan = await plan(router, context)
    manager = FailoverManager(
        FallbackConfig(num_retries=retry_count, backoff_max=0, backoff_jitter=False),
        router,
        backend,
        CooldownManager(backend),
    )
    attempts = []

    async def answer(deployment):
        attempts.append(deployment.deployment_id)
        invalidate_candidate_plan_cache(context)
        if deployment.deployment_id != "quality-b":
            raise ServiceUnavailableError(
                message="mock unavailable",
                affects_deployment_health=False,
                routing_failure_action=RoutingFailureAction.RETRY_OR_NEXT,
            )
        return "answer"

    result, served = await manager.execute_with_failover(
        primary_deployment=candidate_plan.deployments[0],
        model_group="selected",
        execute=answer,
        return_deployment=True,
        routing_context=context,
        request_deadline=operation.deadline,
    )
    expected_order = ids(candidate_plan.deployments)
    assert attempts == [expected_order[0]] * (retry_count + 1) + expected_order[1:]
    assert result == "answer" and served.deployment_id == "quality-b"
    assert ids((await plan(router, context)).deployments) == expected_order
    for deployment_id in set(attempts):
        assert await backend.get_active_requests(deployment_id) == 0
    provider.invoke.assert_awaited_once()
