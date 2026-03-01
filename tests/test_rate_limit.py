from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.middleware.rate_limit import _check_and_acquire_rate_limits
from src.models.errors import InvalidRequestError


@pytest.mark.asyncio
async def test_rate_limit_rpm_enforced(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
    }

    r1 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r2 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r3 = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r3.headers.get("Retry-After") is not None


@pytest.mark.asyncio
async def test_rate_limit_org_rpm_enforced_before_key_limit(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
    }
    record = next(iter(test_app.state._test_repo.records.values()))
    record.rpm_limit = 50
    record.org_rpm_limit = 1
    record.organization_id = "org-test"

    ok = await client.post("/v1/chat/completions", headers=headers, json=body)
    blocked = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert ok.status_code == 200
    assert blocked.status_code == 429
    payload = blocked.json()
    assert payload["error"]["code"] == "org_rpm_exceeded"
    assert payload["error"]["param"] == "org_rpm"


@pytest.mark.asyncio
async def test_rate_limit_user_tpm_enforced(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    record = next(iter(test_app.state._test_repo.records.values()))
    record.rpm_limit = 50
    record.tpm_limit = 10000
    record.user_id = "user-test"
    record.user_tpm_limit = 5

    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "x" * 200}],
    }
    blocked = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert blocked.status_code == 429
    payload = blocked.json()
    assert payload["error"]["code"] == "user_tpm_exceeded"
    assert payload["error"]["param"] == "user_tpm"


@pytest.mark.asyncio
async def test_rate_limit_rejects_unreadable_request_body():
    class RaisingRequest:
        def __init__(self) -> None:
            self.state = SimpleNamespace(
                _rate_limit_checked=False,
                user_api_key=SimpleNamespace(
                    key_rpm_limit=None,
                    key_tpm_limit=None,
                    rpm_limit=10,
                    tpm_limit=1000,
                    organization_id=None,
                    org_rpm_limit=None,
                    org_tpm_limit=None,
                    team_id=None,
                    team_rpm_limit=None,
                    team_tpm_limit=None,
                    user_id=None,
                    user_rpm_limit=None,
                    user_tpm_limit=None,
                    api_key="sk-test",
                    max_parallel_requests=5,
                ),
            )
            self.app = SimpleNamespace(state=SimpleNamespace(limit_counter=SimpleNamespace()))

        async def body(self) -> bytes:
            raise RuntimeError("body stream failed")

    request = RaisingRequest()
    with pytest.raises(InvalidRequestError):
        await _check_and_acquire_rate_limits(request)  # type: ignore[arg-type]
