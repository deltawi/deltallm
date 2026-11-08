"""The live HTTP path preserves its precise charge through the native receipt."""

from decimal import Decimal

from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.spend.spend import SpendTrackingService
from src.billing.spend.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from tests.test_accounting_request_path import _AccountingRepository
from tests.test_spend_operation_http import headers


async def test_live_token_charge_at_its_allowance_preserves_small_configured_price(
    client, test_app
):
    amount = Decimal("0.000000000075")
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info.update(
        input_cost_per_token="0", output_cost_per_token="0", cost_per_request=str(amount)
    )
    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    test_app.state.accounting_protocol_enabled = True
    test_app.state.accounting_protocol_service = accounting
    test_app.state.accounting_max_provider_attempts = 1
    test_app.state.spend_tracking_service = SpendIngestionService(
        db_client=None,
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
        accounting=accounting,
    )
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers=headers(test_app),
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        await accounting.close()

    assert response.status_code == 200, response.text
    (reservation,) = repository.reservations
    (terminal,) = repository.finalizations
    assert terminal.outcome is AccountingOutcome.COMPLETED
    assert terminal.exact_charge == reservation.allowance == amount
