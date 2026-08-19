from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src.models.errors import RateLimitError
from src.services.limit_counter import LimitCounter
from src.services.preflight_capacity import acquire_preflight_capacity, release_preflight_capacity


def _request(limiter: LimitCounter, *, global_limit: int = 1, org_limit: int = 1) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            limit_counter=limiter,
            app_config=SimpleNamespace(
                general_settings=SimpleNamespace(
                    gateway_preflight_capacity_enabled=True,
                    gateway_preflight_global_max_parallel=global_limit,
                    gateway_preflight_org_max_parallel=org_limit,
                    gateway_preflight_lease_ttl_seconds=30,
                )
            ),
        )
    )
    return Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "app": app})


@pytest.mark.asyncio
async def test_preflight_capacity_rejects_before_expensive_work_and_releases() -> None:
    limiter = LimitCounter()
    auth = SimpleNamespace(organization_id="org-a")
    first = _request(limiter)
    second = _request(limiter)

    await acquire_preflight_capacity(first, auth=auth)
    with pytest.raises(RateLimitError, match="Parallel request limit exceeded"):
        await acquire_preflight_capacity(second, auth=auth)

    await release_preflight_capacity(first)
    third = _request(limiter)
    await acquire_preflight_capacity(third, auth=auth)
    await release_preflight_capacity(third)


@pytest.mark.asyncio
async def test_preflight_capacity_acquisition_is_idempotent_per_request() -> None:
    limiter = LimitCounter()
    request = _request(limiter)
    auth = SimpleNamespace(organization_id="org-a")

    await acquire_preflight_capacity(request, auth=auth)
    original = request.state._preflight_capacity_leases
    await acquire_preflight_capacity(request, auth=auth)

    assert request.state._preflight_capacity_leases == original
    assert len(original) == 2
    await release_preflight_capacity(request)
