from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.billing.budget import BudgetEnforcementService, BudgetExceeded, _next_reset_after
from src.billing.spend import SpendTrackingService
from src.billing.spend_events import build_spend_event


class RecordingDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def query_raw(self, query: str, *args):
        self.calls.append((query, args))
        return []

    async def execute_raw(self, query: str, *args):
        self.calls.append((query, args))
        return None


class DuplicateSpendEventDB(RecordingDB):
    async def query_raw(self, query: str, *args):
        self.calls.append((query, args))
        return []


class FailingSpendEventDB:
    async def query_raw(self, query: str, *args):
        del query, args
        raise RuntimeError("spend event insert failed")


class BudgetDB:
    async def query_raw(self, query: str, *args):
        normalized = " ".join(query.lower().split())
        if "from deltallm_verificationtoken" in normalized:
            return [
                {
                    "entity_id": args[0],
                    "max_budget": 10.0,
                    "soft_budget": 8.0,
                    "spend": 12.0,
                    "budget_duration": None,
                    "budget_reset_at": None,
                }
            ]
        return []


class TeamModelBudgetDB:
    def __init__(self, *, counter_spend: float | None) -> None:
        self.counter_spend = counter_spend
        self.calls: list[tuple[str, tuple]] = []

    async def query_raw(self, query: str, *args):
        self.calls.append((query, args))
        normalized = " ".join(query.lower().split())
        if "from deltallm_teamtable" in normalized:
            return [{"model_max_budget": {"gpt-4o-mini": 10.0}}]
        if "from deltallm_teammodelspend" in normalized:
            if self.counter_spend is None:
                return []
            return [{"spend": self.counter_spend}]
        if "from deltallm_spendlog_events" in normalized:
            return [{"total": 12.0}]
        return []


class OrgBudgetDB:
    async def query_raw(self, query: str, *args):
        normalized = " ".join(query.lower().split())
        if "from deltallm_organizationtable" in normalized:
            return [
                {
                    "entity_id": args[0],
                    "max_budget": 40.0,
                    "soft_budget": 20.0,
                    "spend": 25.0,
                    "budget_duration": None,
                    "budget_reset_at": None,
                }
            ]
        return []


class ResettingOrgBudgetDB:
    def __init__(self, *, update_count: int = 1, conflict_row: dict | None = None, metadata: dict | None = None) -> None:
        self.query_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self.update_count = update_count
        self.conflict_row = conflict_row
        self.organization = {
            "entity_id": "org_1",
            "max_budget": 10.0,
            "soft_budget": None,
            "spend": 12.0,
            "budget_duration": "1d",
            "budget_reset_at": datetime(2026, 1, 1, tzinfo=UTC),
            "metadata": metadata or {},
        }

    async def query_raw(self, query: str, *args):
        self.query_calls.append((query, args))
        normalized = " ".join(query.lower().split())
        if "from deltallm_organizationtable" in normalized:
            return [dict(self.organization)]
        return []

    async def execute_raw(self, query: str, *args):
        self.execute_calls.append((query, args))
        normalized = " ".join(query.lower().split())
        if "update deltallm_organizationtable" not in normalized:
            return self.update_count
        if self.update_count <= 0:
            if self.conflict_row is not None:
                self.organization.update(self.conflict_row)
            return 0
        self.organization["spend"] = 0.0
        self.organization["budget_reset_at"] = args[0]
        inferred_anchor_day = args[3] if len(args) > 3 else None
        if inferred_anchor_day is not None:
            metadata = dict(self.organization.get("metadata") or {})
            settings = dict(metadata.get("_budget_reset") or {})
            settings["monthly_anchor_day"] = inferred_anchor_day
            metadata["_budget_reset"] = settings
            self.organization["metadata"] = metadata
        return self.update_count


class RecordingAlertService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_budget_alert(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append(kwargs)


class SpendQueryDB:
    async def query_raw(self, query: str, *args):
        normalized = " ".join(query.lower().split())
        if "total_requests" in normalized and "from deltallm_spendlog_events" in normalized:
            return [
                {
                    "total_spend": 1.25,
                    "total_tokens": 200,
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_requests": 5,
                }
            ]
        if "count(*) as total" in normalized:
            return [{"total": 1}]
        if "from deltallm_spendlog_events" in normalized and "order by start_time desc" in normalized:
            return [
                {
                    "id": "log_1",
                    "request_id": "req_1",
                    "call_type": "completion",
                    "model": "gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "hashed-key",
                    "spend": 0.01,
                    "total_tokens": 20,
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "prompt_tokens_cached": 10,
                    "completion_tokens_cached": 0,
                    "start_time": "2026-02-13T00:00:00+00:00",
                    "end_time": "2026-02-13T00:00:01+00:00",
                    "user": "u1",
                    "team_id": "t1",
                    "metadata": {"error": {"message": "upstream unavailable"}},
                    "cache_hit": False,
                    "status": "error",
                    "http_status_code": 503,
                    "error_type": "HTTPStatusError",
                    "request_tags": ["tag-a"],
                }
            ]
        return []


@pytest.mark.asyncio
async def test_spend_tracking_writes_log_and_ledger_updates():
    db = RecordingDB()
    service = SpendTrackingService(db_client=db)

    await service.log_spend(
        request_id="req_123",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id="end_1",
        model="gpt-4o-mini",
        call_type="completion",
        usage={"total_tokens": 10, "prompt_tokens": 6, "completion_tokens": 4},
        cost=0.02,
        metadata={"api_base": "https://api.openai.com/v1", "tags": ["prod"]},
    )

    assert any("insert into deltallm_spendlog_events" in q.lower() for q, _ in db.calls)
    assert not any("insert into deltallm_spendlogs" in q.lower() for q, _ in db.calls)
    assert any("update deltallm_verificationtoken" in q.lower() for q, _ in db.calls)
    assert any("update deltallm_usertable" in q.lower() for q, _ in db.calls)
    assert any("update deltallm_teamtable" in q.lower() for q, _ in db.calls)
    assert any("update deltallm_organizationtable" in q.lower() for q, _ in db.calls)


@pytest.mark.asyncio
async def test_spend_tracking_writes_normalized_event_and_team_model_counter():
    db = RecordingDB()
    service = SpendTrackingService(db_client=db)

    await service.log_spend(
        request_id="req_audio_123",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id="end_1",
        model="gpt-4o-mini-tts",
        call_type="audio_speech",
        usage={"input_characters": 1200},
        cost=0.02,
        metadata={
            "api_base": "https://api.openai.com/v1",
            "billing": {
                "billing_unit": "character",
                "pricing_fields_used": ["input_cost_per_character"],
                "usage_snapshot": {"input_characters": 1200},
            },
        },
    )

    event_call = next(args for query, args in db.calls if "insert into deltallm_spendlog_events" in query.lower())
    assert event_call[6] == "org_1"
    assert event_call[10] == "openai"
    assert event_call[14] == "character"
    assert event_call[23] == 1200
    assert any("insert into deltallm_teammodelspend" in q.lower() for q, _ in db.calls)


@pytest.mark.asyncio
async def test_log_spend_once_returns_duplicate_without_ledger_update():
    db = DuplicateSpendEventDB()
    service = SpendTrackingService(db_client=db)

    result = await service.log_spend_once(
        event_id="event-1",
        request_id="req-1",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id=None,
        model="gpt-4o-mini",
        call_type="completion",
        usage={"total_tokens": 10, "prompt_tokens": 6, "completion_tokens": 4},
        cost=0.02,
        metadata={"api_base": "https://api.openai.com/v1"},
    )

    assert result == "duplicate"
    assert any("insert into deltallm_spendlog_events" in q.lower() for q, _ in db.calls)
    assert not any("update deltallm_verificationtoken" in q.lower() for q, _ in db.calls)


@pytest.mark.asyncio
async def test_log_spend_once_raises_on_write_failure():
    service = SpendTrackingService(db_client=FailingSpendEventDB())

    with pytest.raises(RuntimeError, match="spend event insert failed"):
        await service.log_spend_once(
            event_id="event-1",
            request_id="req-1",
            api_key="key_hash",
            user_id="user_1",
            team_id="team_1",
            organization_id="org_1",
            end_user_id=None,
            model="gpt-4o-mini",
            call_type="completion",
            usage={"total_tokens": 10, "prompt_tokens": 6, "completion_tokens": 4},
            cost=0.02,
            metadata={"api_base": "https://api.openai.com/v1"},
        )


@pytest.mark.asyncio
async def test_spend_tracking_counts_audio_tokens_in_total_tokens_when_explicit_total_missing():
    db = RecordingDB()
    service = SpendTrackingService(db_client=db)

    await service.log_spend(
        request_id="req_audio_tokens",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id=None,
        model="gpt-4o-mini-tts",
        call_type="audio_speech",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "input_audio_tokens": 3,
            "output_audio_tokens": 5,
        },
        cost=0.47,
        metadata={
            "api_base": "https://api.openai.com/v1",
            "billing": {
                "billing_unit": "token",
                "usage_snapshot": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "input_audio_tokens": 3,
                    "output_audio_tokens": 5,
                },
            },
        },
    )

    event_call = next(args for query, args in db.calls if "insert into deltallm_spendlog_events" in query.lower())
    assert event_call[16] == 22
    assert event_call[21] == 3
    assert event_call[22] == 5


@pytest.mark.asyncio
async def test_spend_tracking_persists_cached_token_counts():
    db = RecordingDB()
    service = SpendTrackingService(db_client=db)

    await service.log_spend(
        request_id="req_cached",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id=None,
        model="gpt-4o-mini",
        call_type="completion",
        usage={
            "total_tokens": 10,
            "prompt_tokens": 6,
            "completion_tokens": 4,
            "prompt_tokens_cached": 6,
            "completion_tokens_cached": 0,
        },
        cost=0.01,
        metadata={"api_base": "cache"},
        cache_hit=True,
    )

    insert_call = next(args for query, args in db.calls if "insert into deltallm_spendlog_events" in query.lower())
    assert insert_call[19] == 6
    assert insert_call[20] == 0


def test_build_spend_event_infers_groq_from_openai_compatible_api_base() -> None:
    event = build_spend_event(
        request_id="req_groq",
        api_key="key_hash",
        user_id=None,
        team_id=None,
        organization_id=None,
        end_user_id=None,
        model="openai/gpt-oss-20b",
        call_type="completion",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        cost=0.01,
        metadata={"api_base": "https://api.groq.com/openai/v1"},
        cache_hit=False,
        start_time=datetime.now(tz=UTC),
        end_time=datetime.now(tz=UTC),
    )

    assert event["provider"] == "groq"


@pytest.mark.asyncio
async def test_spend_tracking_preserves_explicit_provider_for_cache_rows():
    db = RecordingDB()
    service = SpendTrackingService(db_client=db)

    await service.log_spend(
        request_id="req_cached_groq",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id=None,
        model="openai/gpt-oss-20b",
        call_type="completion",
        usage={
            "total_tokens": 10,
            "prompt_tokens": 6,
            "completion_tokens": 4,
            "prompt_tokens_cached": 6,
            "completion_tokens_cached": 0,
        },
        cost=0.01,
        metadata={
            "api_base": "cache",
            "provider": "groq",
            "deployment_model": "openai/gpt-oss-20b",
        },
        cache_hit=True,
    )

    insert_call = next(args for query, args in db.calls if "insert into deltallm_spendlog_events" in query.lower())
    assert insert_call[10] == "groq"


@pytest.mark.asyncio
async def test_request_failure_logging_writes_error_event_without_ledger_updates():
    db = RecordingDB()
    service = SpendTrackingService(db_client=db)

    await service.log_request_failure(
        request_id="req_failure",
        api_key="key_hash",
        user_id="user_1",
        team_id="team_1",
        organization_id="org_1",
        end_user_id=None,
        model="gpt-4o-mini",
        call_type="completion",
        metadata={"api_base": "https://api.openai.com/v1"},
        http_status_code=503,
        exc=RuntimeError("upstream unavailable"),
    )

    event_call = next(args for query, args in db.calls if "insert into deltallm_spendlog_events" in query.lower())
    assert event_call[12] == 0.0
    assert event_call[16] == 0
    assert event_call[38] == "error"
    assert event_call[39] == 503
    assert event_call[40] == "RuntimeError"
    assert not any("update deltallm_verificationtoken" in q.lower() for q, _ in db.calls)
    assert not any("update deltallm_usertable" in q.lower() for q, _ in db.calls)
    assert not any("update deltallm_teamtable" in q.lower() for q, _ in db.calls)
    assert not any("update deltallm_organizationtable" in q.lower() for q, _ in db.calls)


@pytest.mark.asyncio
async def test_budget_enforcement_raises_when_hard_budget_exceeded():
    service = BudgetEnforcementService(db_client=BudgetDB())

    with pytest.raises(BudgetExceeded):
        await service.check_budgets(
            api_key="key_hash",
            user_id=None,
            team_id=None,
            organization_id=None,
            model="gpt-4o-mini",
        )


@pytest.mark.asyncio
async def test_budget_enforcement_prefers_team_model_counter_when_available():
    db = TeamModelBudgetDB(counter_spend=12.0)
    service = BudgetEnforcementService(db_client=db)

    with pytest.raises(BudgetExceeded):
        await service.check_budgets(
            api_key=None,
            user_id=None,
            team_id="team_1",
            organization_id=None,
            model="gpt-4o-mini",
        )

    assert any("from deltallm_teammodelspend" in query.lower() for query, _ in db.calls)
    assert not any("from deltallm_spendlog_events" in query.lower() for query, _ in db.calls)


@pytest.mark.asyncio
async def test_budget_enforcement_falls_back_to_spend_events_when_counter_missing():
    db = TeamModelBudgetDB(counter_spend=None)
    service = BudgetEnforcementService(db_client=db)

    with pytest.raises(BudgetExceeded):
        await service.check_budgets(
            api_key=None,
            user_id=None,
            team_id="team_1",
            organization_id=None,
            model="gpt-4o-mini",
        )

    assert any("from deltallm_spendlog_events" in query.lower() for query, _ in db.calls)


@pytest.mark.asyncio
async def test_budget_enforcement_sends_org_soft_budget_alerts() -> None:
    alert_service = RecordingAlertService()
    service = BudgetEnforcementService(db_client=OrgBudgetDB(), alert_service=alert_service)

    await service.check_budgets(
        api_key=None,
        user_id=None,
        team_id=None,
        organization_id="org_1",
    )

    assert alert_service.calls == [
        {
            "entity_type": "org",
            "entity_id": "org_1",
            "current_spend": 25.0,
            "soft_budget": 20.0,
            "hard_budget": 40.0,
        }
    ]


def test_next_reset_after_preserves_hour_and_day_durations() -> None:
    now = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)

    assert _next_reset_after(
        duration="1h",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=now,
    ) == datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    assert _next_reset_after(
        duration="30d",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=now,
    ) == datetime(2026, 1, 31, 0, 0, tzinfo=UTC)


def test_next_reset_after_advances_old_fixed_duration_without_iterating_windows() -> None:
    assert _next_reset_after(
        duration="1h",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 3, 12, 30, tzinfo=UTC),
    ) == datetime(2026, 1, 3, 13, 0, tzinfo=UTC)


def test_next_reset_after_supports_custom_duration_values() -> None:
    assert _next_reset_after(
        duration="2h",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 1, 1, 30, tzinfo=UTC),
    ) == datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    assert _next_reset_after(
        duration="14d",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    ) == datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    assert _next_reset_after(
        duration="3mo",
        previous_reset_at=datetime(2026, 1, 31, 0, 0, tzinfo=UTC),
        now=datetime(2026, 2, 1, 0, 0, tzinfo=UTC),
        monthly_anchor_day=31,
    ) == datetime(2026, 4, 30, 0, 0, tzinfo=UTC)


def test_next_reset_after_supports_calendar_months() -> None:
    assert _next_reset_after(
        duration="1mo",
        previous_reset_at=datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 15, 0, 1, tzinfo=UTC),
    ) == datetime(2026, 2, 15, 0, 0, tzinfo=UTC)
    feb_reset = _next_reset_after(
        duration="1mo",
        previous_reset_at=datetime(2026, 1, 31, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 31, 0, 1, tzinfo=UTC),
    )
    assert feb_reset == datetime(2026, 2, 28, 0, 0, tzinfo=UTC)
    assert _next_reset_after(
        duration="1mo",
        previous_reset_at=feb_reset,
        now=datetime(2026, 2, 28, 0, 1, tzinfo=UTC),
    ) == datetime(2026, 3, 31, 0, 0, tzinfo=UTC)
    leap_feb_reset = _next_reset_after(
        duration="1mo",
        previous_reset_at=datetime(2028, 1, 31, 0, 0, tzinfo=UTC),
        now=datetime(2028, 1, 31, 0, 1, tzinfo=UTC),
    )
    assert leap_feb_reset == datetime(2028, 2, 29, 0, 0, tzinfo=UTC)
    assert _next_reset_after(
        duration="1mo",
        previous_reset_at=leap_feb_reset,
        now=datetime(2028, 2, 29, 0, 1, tzinfo=UTC),
    ) == datetime(2028, 3, 31, 0, 0, tzinfo=UTC)
    assert _next_reset_after(
        duration="1mo",
        previous_reset_at=datetime(2026, 12, 31, 0, 0, tzinfo=UTC),
        now=datetime(2026, 12, 31, 0, 1, tzinfo=UTC),
    ) == datetime(2027, 1, 31, 0, 0, tzinfo=UTC)


def test_next_reset_after_uses_monthly_anchor_day() -> None:
    feb_reset = _next_reset_after(
        duration="1mo",
        previous_reset_at=datetime(2026, 1, 30, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 30, 0, 1, tzinfo=UTC),
        monthly_anchor_day=30,
    )

    assert feb_reset == datetime(2026, 2, 28, 0, 0, tzinfo=UTC)
    assert _next_reset_after(
        duration="1mo",
        previous_reset_at=feb_reset,
        now=datetime(2026, 2, 28, 0, 1, tzinfo=UTC),
        monthly_anchor_day=30,
    ) == datetime(2026, 3, 30, 0, 0, tzinfo=UTC)


def test_next_reset_after_advances_missed_monthly_windows() -> None:
    assert _next_reset_after(
        duration="1mo",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=datetime(2026, 4, 28, 9, 0, tzinfo=UTC),
    ) == datetime(2026, 5, 1, 0, 0, tzinfo=UTC)


def test_next_reset_after_advances_old_monthly_windows_without_iterating_windows() -> None:
    assert _next_reset_after(
        duration="1mo",
        previous_reset_at=datetime(2020, 1, 31, 0, 0, tzinfo=UTC),
        now=datetime(2026, 4, 29, 0, 0, tzinfo=UTC),
        monthly_anchor_day=31,
    ) == datetime(2026, 4, 30, 0, 0, tzinfo=UTC)


def test_next_reset_after_rejects_unsupported_duration() -> None:
    assert _next_reset_after(
        duration="monthly",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
    ) is None
    assert _next_reset_after(
        duration="10001d",
        previous_reset_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
    ) is None


def test_next_reset_after_returns_none_when_duration_overflows_datetime() -> None:
    assert _next_reset_after(
        duration="10000mo",
        previous_reset_at=datetime(9990, 1, 1, 0, 0, tzinfo=UTC),
        now=datetime(9990, 1, 2, 0, 0, tzinfo=UTC),
    ) is None


@pytest.mark.asyncio
async def test_budget_enforcement_resets_due_budget_before_hard_budget_check() -> None:
    db = ResettingOrgBudgetDB()
    service = BudgetEnforcementService(db_client=db)

    await service.check_budgets(
        api_key=None,
        user_id=None,
        team_id=None,
        organization_id="org_1",
    )

    assert db.organization["spend"] == 0.0
    assert db.organization["budget_reset_at"].tzinfo is None
    assert db.organization["budget_reset_at"].replace(tzinfo=UTC) > datetime.now(tz=UTC)
    assert not any("update deltallm_organizationtable" in query.lower() for query, _ in db.query_calls)
    assert any("update deltallm_organizationtable" in query.lower() for query, _ in db.execute_calls)
    reset_query, reset_args = db.execute_calls[0]
    assert "budget_reset_at is not distinct from $3::timestamp" in " ".join(reset_query.lower().split())
    assert reset_args[2] == datetime(2026, 1, 1)
    assert reset_args[3] is None


@pytest.mark.asyncio
async def test_budget_enforcement_infers_and_persists_missing_monthly_anchor() -> None:
    db = ResettingOrgBudgetDB(metadata={})
    db.organization["budget_duration"] = "1mo"
    db.organization["budget_reset_at"] = datetime(2026, 1, 30, tzinfo=UTC)
    service = BudgetEnforcementService(db_client=db)

    await service.check_budgets(
        api_key=None,
        user_id=None,
        team_id=None,
        organization_id="org_1",
    )

    assert db.organization["spend"] == 0.0
    assert db.organization["metadata"]["_budget_reset"]["monthly_anchor_day"] == 30
    reset_query, reset_args = db.execute_calls[0]
    assert "jsonb_set" in reset_query
    assert reset_args[3] == 30


@pytest.mark.asyncio
async def test_budget_enforcement_ignores_out_of_range_legacy_duration() -> None:
    db = ResettingOrgBudgetDB()
    db.organization["spend"] = 3.0
    db.organization["budget_duration"] = "10001d"
    service = BudgetEnforcementService(db_client=db)

    await service.check_budgets(
        api_key=None,
        user_id=None,
        team_id=None,
        organization_id="org_1",
    )

    assert db.organization["spend"] == 3.0
    assert db.execute_calls == []


@pytest.mark.asyncio
async def test_budget_enforcement_refetches_when_reset_guard_conflicts() -> None:
    future_reset = datetime.now(tz=UTC) + timedelta(days=1)
    db = ResettingOrgBudgetDB(
        update_count=0,
        conflict_row={
            "spend": 3.0,
            "budget_reset_at": future_reset,
        },
    )
    service = BudgetEnforcementService(db_client=db)

    await service.check_budgets(
        api_key=None,
        user_id=None,
        team_id=None,
        organization_id="org_1",
    )

    assert db.organization["spend"] == 3.0
    assert db.organization["budget_reset_at"] == future_reset
    assert len(db.execute_calls) == 1
    assert len(db.query_calls) == 2


@pytest.mark.asyncio
async def test_spend_logs_endpoint_returns_paginated_data(client, test_app):
    test_app.state.prisma_manager = SimpleNamespace(client=SpendQueryDB())
    test_app.state.settings.master_key = "mk-test"

    response = await client.get(
        "/spend/logs",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["logs"][0]["request_id"] == "req_1"
    assert payload["logs"][0]["status"] == "error"
    assert payload["logs"][0]["http_status_code"] == 503
    assert payload["logs"][0]["error_type"] == "HTTPStatusError"


@pytest.mark.asyncio
async def test_spend_logs_endpoint_reads_normalized_events(client, test_app):
    test_app.state.prisma_manager = SimpleNamespace(client=SpendQueryDB())
    test_app.state.settings.master_key = "mk-test"

    response = await client.get(
        "/spend/logs",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["logs"][0]["request_id"] == "req_1"


@pytest.mark.asyncio
async def test_global_spend_summary_endpoint(client, test_app):
    test_app.state.prisma_manager = SimpleNamespace(client=SpendQueryDB())
    test_app.state.settings.master_key = "mk-test"

    response = await client.get(
        "/global/spend",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_spend"] == 1.25
    assert payload["total_requests"] == 5
