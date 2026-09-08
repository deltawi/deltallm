import asyncio
from unittest.mock import AsyncMock

import httpx

from src.router.selection.economics import AccountedSelectorHop
from tests.router.selection.provider_fixtures import PROMPT, bridge, response_body
from tests.test_operation_reservation import make_operation


async def test_malformed_selector_result_preserves_authoritative_paid_usage():
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
    body = response_body()
    body["choices"] = []
    billing = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        hop = AccountedSelectorHop(store=billing, operation=operation, hop=bridge(client))
        outcome = await hop.invoke(
            deployment_id="classifier-concrete",
            prompt=PROMPT,
            expires_at=asyncio.get_running_loop().time() + 2,
        )
    assert outcome.usage.kind == "reported"
    billing.dispatch.assert_awaited_once()
    billing.accept_selector.assert_awaited_once()
    charge = billing.accept_selector.call_args.args[1]
    assert charge.usage.total_tokens == 18
    assert charge.customer_charge == charge.provider_cost > 0
