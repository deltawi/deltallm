from __future__ import annotations

import pytest

from src.auth.external_errors import ExternalAuthError
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_auth_exchange import enable, make_new, proof, services

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


async def economic_snapshot(fixture):
    return {
        "organization": await fixture.db.query_raw(
            "SELECT * FROM deltallm_organizationtable WHERE organization_id = $1",
            fixture.organization_id,
        ),
        "team": await fixture.db.query_raw(
            "SELECT * FROM deltallm_teamtable WHERE team_id = $1", fixture.team_id
        ),
        "runtime": await fixture.db.query_raw(
            "SELECT * FROM deltallm_usertable WHERE user_id = $1", fixture.account_id
        ),
        "tiers": await fixture.db.query_raw(
            "SELECT * FROM deltallm_organizationtierassignment WHERE organization_id = $1",
            fixture.organization_id,
        ),
        "spend": await fixture.db.query_raw(
            "SELECT * FROM deltallm_teammodelspend WHERE team_id = $1", fixture.team_id
        ),
    }


async def test_exchange_renewal_denial_and_revoke_preserve_all_existing_economic_rows(
    external_database,
):
    fixture = external_database
    await enable(fixture)
    await fixture.db.execute_raw(
        "UPDATE deltallm_teamtable SET blocked = true, max_budget = 71, spend = 9, spend_exact = 9.123456789, reserved_spend_exact = 1.123456789, rpm_limit = 3, tpm_limit = 44, self_service_keys_enabled = false, self_service_budget_ceiling = 5 WHERE team_id = $1",
        fixture.team_id,
    )
    await fixture.db.execute_raw(
        "UPDATE deltallm_organizationtable SET max_budget = 92, spend = 13, spend_exact = 13.123456789, reserved_spend_exact = 2.123456789, metadata = '{"
        + '"payment_state":"delinquent","subscription":"external"'
        + "}'::jsonb WHERE organization_id = $1",
        fixture.organization_id,
    )
    await fixture.db.execute_raw(
        "UPDATE deltallm_usertable SET blocked = true, max_budget = 12, spend = 4, spend_exact = 4.123456789, reserved_spend_exact = 0.123456789 WHERE user_id = $1",
        fixture.account_id,
    )
    before = await economic_snapshot(fixture)
    exchange, sessions, revoke = services(fixture)
    for index in range(3):
        assertion = proof(fixture)
        await exchange.consume(assertion, "economics")
        response = await exchange.exchange(assertion, "economics")
        assert await sessions.get_context(
            exchange.identities.sessions.hash_token(response.session_token)
        )
        assert await economic_snapshot(fixture) == before
    with pytest.raises(ExternalAuthError):
        await exchange.exchange(proof(fixture, external_customer_id="wrong-customer"), "denied")
    await revoke.revoke(proof(fixture, purpose="gateway_session_revoke"), "revoke")
    assert await economic_snapshot(fixture) == before


async def test_first_provisioning_preserves_existing_organization_and_team_rows(external_database):
    fixture = external_database
    await enable(fixture)
    await make_new(fixture)
    before = await economic_snapshot(fixture)
    exchange, _, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "new-economics")
    after = await economic_snapshot(fixture)
    assert {k: v for k, v in after.items() if k != "runtime"} == {
        k: v for k, v in before.items() if k != "runtime"
    }
    runtime = await fixture.db.query_raw(
        "SELECT spend, spend_exact, reserved_spend_exact, max_budget FROM deltallm_usertable WHERE user_id = $1",
        response.inference_user_id,
    )
    assert runtime[0]["spend"] == 0 and runtime[0]["max_budget"] is None
