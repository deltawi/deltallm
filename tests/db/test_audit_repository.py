from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from src.db.repositories import AuditEventRecord, AuditPayloadRecord, AuditRepository


class FakePrisma:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, object]] = {}
        self.payloads: dict[str, dict[str, object]] = {}
        self.organization_toggles: dict[str, bool] = {}
        self._event_seq = 0
        self._payload_seq = 0

    async def query_raw(self, query: str, *args):
        if "INSERT INTO deltallm_auditevent" in query:
            self._event_seq += 1
            event_id = f"evt-{self._event_seq}"
            metadata = json.loads(str(args[17])) if args[17] is not None else None
            row = {
                "event_id": event_id,
                "occurred_at": datetime.now(tz=UTC).isoformat(),
                "organization_id": args[0],
                "actor_type": args[1],
                "actor_id": args[2],
                "api_key": args[3],
                "action": args[4],
                "resource_type": args[5],
                "resource_id": args[6],
                "request_id": args[7],
                "correlation_id": args[8],
                "ip": args[9],
                "user_agent": args[10],
                "status": args[11],
                "latency_ms": args[12],
                "input_tokens": args[13],
                "output_tokens": args[14],
                "error_type": args[15],
                "error_code": args[16],
                "metadata": metadata,
                "content_stored": args[18],
                "prev_hash": args[19],
                "event_hash": args[20],
            }
            self.events[event_id] = dict(row)
            return [row]

        if "INSERT INTO deltallm_auditpayload" in query:
            self._payload_seq += 1
            payload_id = f"pl-{self._payload_seq}"
            content_json = json.loads(str(args[3])) if args[3] is not None else None
            row = {
                "payload_id": payload_id,
                "event_id": args[0],
                "kind": args[1],
                "storage_mode": args[2],
                "content_json": content_json,
                "storage_uri": args[4],
                "content_sha256": args[5],
                "size_bytes": args[6],
                "redacted": args[7],
                "created_at": datetime.now(tz=UTC).isoformat(),
            }
            self.payloads[payload_id] = dict(row)
            return [row]

        if "SELECT audit_content_storage_enabled" in query:
            organization_id = str(args[0])
            if organization_id not in self.organization_toggles:
                return []
            return [{"audit_content_storage_enabled": self.organization_toggles[organization_id]}]

        return []


@pytest.mark.asyncio
async def test_audit_repository_creates_event_and_payload():
    prisma = FakePrisma()
    repo = AuditRepository(prisma)
    event = await repo.create_event(
        AuditEventRecord(
            event_id="",
            action="CHAT_COMPLETION",
            organization_id="org-1",
            actor_type="api_key",
            actor_id="key-1",
            request_id="req-1",
            metadata={"model": "openai/gpt-4o-mini"},
            content_stored=False,
        )
    )
    assert event.event_id.startswith("evt-")
    assert event.action == "CHAT_COMPLETION"
    assert event.organization_id == "org-1"
    assert event.metadata == {"model": "openai/gpt-4o-mini"}
    assert event.content_stored is False

    payload = await repo.create_payload(
        AuditPayloadRecord(
            payload_id="",
            event_id=event.event_id,
            kind="prompt",
            storage_mode="inline",
            content_json={"messages": [{"role": "user", "content": "hello"}]},
            redacted=False,
        )
    )
    assert payload.payload_id.startswith("pl-")
    assert payload.event_id == event.event_id
    assert payload.kind == "prompt"
    assert payload.content_json == {"messages": [{"role": "user", "content": "hello"}]}


@pytest.mark.asyncio
async def test_audit_repository_org_content_toggle_defaults_to_false():
    prisma = FakePrisma()
    prisma.organization_toggles["org-enabled"] = True
    prisma.organization_toggles["org-disabled"] = False
    repo = AuditRepository(prisma)

    assert await repo.is_content_storage_enabled_for_org("org-enabled") is True
    assert await repo.is_content_storage_enabled_for_org("org-disabled") is False
    assert await repo.is_content_storage_enabled_for_org("org-missing") is False
    assert await repo.is_content_storage_enabled_for_org(None) is False
