from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.tier_policy_invalidation import reload_tier_policy_for_app


class _FakeGovernanceInvalidation:
    def __init__(
        self,
        *,
        fail_local: bool = False,
        fail_notify: bool = False,
        notify_result: bool = True,
    ) -> None:
        self.fail_local = fail_local
        self.fail_notify = fail_notify
        self.notify_result = notify_result
        self.local_targets: list[tuple[str, ...]] = []
        self.notified_targets: list[tuple[str, ...]] = []

    async def invalidate_local(self, *targets: str) -> None:
        self.local_targets.append(tuple(targets))
        if self.fail_local:
            raise RuntimeError("reload failed")

    async def notify(self, *targets: str) -> bool:
        self.notified_targets.append(tuple(targets))
        if self.fail_notify:
            raise RuntimeError("notify failed")
        return self.notify_result


class _FakeTierPolicyService:
    def __init__(self, *, fail_reload: bool = False, mode: str = "shadow") -> None:
        self.fail_reload = fail_reload
        self.mode = mode
        self.reload_calls = 0

    async def reload(self) -> None:
        self.reload_calls += 1
        if self.fail_reload:
            raise RuntimeError("reload failed")


def _app(**state_items: object) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(**state_items))


@pytest.mark.asyncio
async def test_reload_tier_policy_for_app_reports_governance_success() -> None:
    invalidation = _FakeGovernanceInvalidation()

    result = await reload_tier_policy_for_app(_app(governance_invalidation_service=invalidation))

    assert result.to_dict() == {
        "attempted": True,
        "reloaded": True,
        "notified": True,
        "reason": "reloaded_and_notified",
    }
    assert invalidation.local_targets == [("tier_policy",)]
    assert invalidation.notified_targets == [("tier_policy",)]


@pytest.mark.asyncio
async def test_reload_tier_policy_for_app_reports_local_reload_failure() -> None:
    invalidation = _FakeGovernanceInvalidation(fail_local=True)

    result = await reload_tier_policy_for_app(_app(governance_invalidation_service=invalidation))

    payload = result.to_dict()
    assert payload["attempted"] is True
    assert payload["reloaded"] is False
    assert payload["notified"] is True
    assert payload["reason"] == "local_reload_failed_remote_notified"
    assert "reload failed" in payload["error"]
    assert invalidation.notified_targets == [("tier_policy",)]


@pytest.mark.asyncio
async def test_reload_tier_policy_for_app_reports_local_reload_and_notify_failure() -> None:
    invalidation = _FakeGovernanceInvalidation(fail_local=True, fail_notify=True)

    result = await reload_tier_policy_for_app(_app(governance_invalidation_service=invalidation))

    payload = result.to_dict()
    assert payload["attempted"] is True
    assert payload["reloaded"] is False
    assert payload["notified"] is False
    assert payload["reason"] == "local_reload_failed_remote_notify_failed"
    assert "reload failed" in payload["error"]
    assert "notify failed" in payload["error"]


@pytest.mark.asyncio
async def test_reload_tier_policy_for_app_reports_notify_unavailable() -> None:
    invalidation = _FakeGovernanceInvalidation(notify_result=False)

    result = await reload_tier_policy_for_app(_app(governance_invalidation_service=invalidation))

    assert result.to_dict() == {
        "attempted": True,
        "reloaded": True,
        "notified": False,
        "reason": "remote_notify_unavailable",
    }


@pytest.mark.asyncio
async def test_reload_tier_policy_for_app_uses_direct_service_without_governance() -> None:
    service = _FakeTierPolicyService()

    result = await reload_tier_policy_for_app(_app(tier_policy_service=service))

    assert service.reload_calls == 1
    assert result.to_dict() == {
        "attempted": True,
        "reloaded": True,
        "notified": False,
        "reason": "reloaded_without_broadcast",
    }


@pytest.mark.asyncio
async def test_reload_tier_policy_for_app_skips_disabled_service() -> None:
    service = _FakeTierPolicyService(mode="disabled")
    invalidation = _FakeGovernanceInvalidation()

    result = await reload_tier_policy_for_app(
        _app(
            tier_policy_service=service,
            governance_invalidation_service=invalidation,
        )
    )

    assert service.reload_calls == 0
    assert invalidation.local_targets == []
    assert invalidation.notified_targets == []
    assert result.to_dict() == {
        "attempted": False,
        "reloaded": False,
        "notified": False,
        "reason": "tier_policy_disabled",
    }


@pytest.mark.asyncio
async def test_reload_tier_policy_for_app_reports_missing_service() -> None:
    result = await reload_tier_policy_for_app(_app())

    assert result.to_dict() == {
        "attempted": False,
        "reloaded": False,
        "notified": False,
        "reason": "service_unavailable",
    }
