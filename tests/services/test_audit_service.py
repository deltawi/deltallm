from __future__ import annotations

import asyncio

import pytest

from src.db.repositories import AuditEventRecord, AuditPayloadRecord
from src.services.audit_service import AuditEventInput, AuditPayloadInput, AuditService


class FakeAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEventRecord] = []
        self.payloads: list[AuditPayloadRecord] = []
        self.content_toggles: dict[str, bool] = {}

    async def is_content_storage_enabled_for_org(self, organization_id: str | None) -> bool:
        if not organization_id:
            return False
        return self.content_toggles.get(organization_id, False)

    async def create_event(self, record: AuditEventRecord) -> AuditEventRecord:
        event_id = f"evt-{len(self.events) + 1}"
        stored = AuditEventRecord(**{**record.__dict__, "event_id": event_id})
        self.events.append(stored)
        return stored

    async def create_payload(self, record: AuditPayloadRecord) -> AuditPayloadRecord:
        payload_id = f"pl-{len(self.payloads) + 1}"
        stored = AuditPayloadRecord(**{**record.__dict__, "payload_id": payload_id})
        self.payloads.append(stored)
        return stored


@pytest.mark.asyncio
async def test_audit_service_enforces_org_content_toggle():
    repo = FakeAuditRepository()
    repo.content_toggles = {"org-enabled": True, "org-disabled": False}
    service = AuditService(repo)

    await service.record_event_sync(
        AuditEventInput(action="CHAT_COMPLETION", organization_id="org-enabled"),
        payloads=[AuditPayloadInput(kind="prompt", content_json={"messages": [{"role": "user", "content": "hello"}]})],
    )
    await service.record_event_sync(
        AuditEventInput(action="CHAT_COMPLETION", organization_id="org-disabled"),
        payloads=[AuditPayloadInput(kind="prompt", content_json={"messages": [{"role": "user", "content": "secret"}]})],
    )

    assert len(repo.events) == 2
    assert repo.events[0].content_stored is True
    assert repo.events[1].content_stored is False
    assert repo.payloads[0].content_json is not None
    assert repo.payloads[1].content_json is None
    assert repo.payloads[1].redacted is True


@pytest.mark.asyncio
async def test_audit_service_drops_non_critical_when_queue_full():
    repo = FakeAuditRepository()
    service = AuditService(repo, queue_max_size=1)

    service.record_event(AuditEventInput(action="FIRST", organization_id="org-1"), critical=False)
    service.record_event(AuditEventInput(action="SECOND", organization_id="org-1"), critical=False)
    assert service.dropped_events == 1

    await service.start()
    await asyncio.sleep(0.05)
    await service.shutdown()
    assert [event.action for event in repo.events] == ["FIRST"]


@pytest.mark.asyncio
async def test_audit_service_critical_fallback_when_queue_full():
    repo = FakeAuditRepository()
    service = AuditService(repo, queue_max_size=1)

    service.record_event(AuditEventInput(action="FIRST", organization_id="org-1"), critical=False)
    service.record_event(AuditEventInput(action="SECOND", organization_id="org-1"), critical=True)

    await service.start()
    await asyncio.sleep(0.1)
    await service.shutdown()

    assert service.dropped_events == 0
    assert sorted(event.action for event in repo.events) == ["FIRST", "SECOND"]
