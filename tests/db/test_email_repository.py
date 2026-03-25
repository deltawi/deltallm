from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from src.db.email import EmailOutboxRecord, EmailOutboxRepository


class FakePrisma:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    async def query_raw(self, query: str, *args):
        if "INSERT INTO deltallm_emailoutbox" in query:
            email_id = str(args[0])
            row = {
                "email_id": email_id,
                "kind": str(args[1]),
                "provider": str(args[2]),
                "to_addresses": list(args[3]),
                "cc_addresses": list(args[4]),
                "bcc_addresses": list(args[5]),
                "from_address": str(args[6]),
                "reply_to": args[7],
                "template_key": args[8],
                "payload_json": json.loads(str(args[9])) if args[9] is not None else None,
                "subject": str(args[10]),
                "text_body": str(args[11]),
                "html_body": args[12],
                "status": str(args[13]),
                "attempt_count": int(args[14]),
                "max_attempts": int(args[15]),
                "next_attempt_at": args[16],
                "last_error": args[17],
                "last_provider_message_id": args[18],
                "created_by_account_id": args[19],
                "created_at": datetime.now(tz=UTC).isoformat(),
                "updated_at": datetime.now(tz=UTC).isoformat(),
                "sent_at": args[20],
            }
            self.rows[email_id] = row
            return [dict(row)]

        if "WITH due AS" in query:
            limit = int(args[0])
            due_rows = [
                row
                for row in self.rows.values()
                if row["status"] in {"queued", "retrying"} and row["next_attempt_at"] <= datetime.now(tz=UTC)
            ][:limit]
            claimed: list[dict[str, object]] = []
            for row in due_rows:
                updated = dict(row)
                updated["status"] = "sending"
                updated["attempt_count"] = int(updated["attempt_count"]) + 1
                updated["updated_at"] = datetime.now(tz=UTC).isoformat()
                self.rows[str(updated["email_id"])] = updated
                claimed.append(updated)
            return claimed

        if "SELECT COUNT(*)::int AS count" in query:
            count = sum(1 for row in self.rows.values() if row["status"] in {"queued", "retrying"})
            return [{"count": count}]

        if "GROUP BY status" in query:
            grouped: dict[str, int] = {}
            for row in self.rows.values():
                status = str(row["status"])
                grouped[status] = grouped.get(status, 0) + 1
            return [{"status": status, "count": count} for status, count in sorted(grouped.items())]

        if "ORDER BY created_at DESC" in query:
            values = sorted(
                self.rows.values(),
                key=lambda item: str(item["created_at"]),
                reverse=True,
            )
            return [dict(row) for row in values[: int(args[0])]]

        if "DELETE FROM deltallm_emailoutbox" in query:
            before = args[0]
            removed: list[dict[str, object]] = []
            for email_id, row in list(self.rows.items()):
                if row["status"] in {"sent", "failed", "cancelled"} and row["updated_at"] < before:
                    removed.append({"email_id": email_id})
                    self.rows.pop(email_id, None)
            return removed

        if "WHERE email_id = $1" in query:
            row = self.rows.get(str(args[0]))
            return [dict(row)] if row else []

        return []

    async def execute_raw(self, query: str, *args):
        email_id = str(args[0])
        row = dict(self.rows[email_id])
        if "SET to_addresses = $2::text[]" in query:
            row["to_addresses"] = list(args[1])
            row["cc_addresses"] = list(args[2])
            row["bcc_addresses"] = list(args[3])
            row["payload_json"] = json.loads(str(args[4])) if args[4] is not None else None
        elif "SET status = 'sent'" in query:
            row["status"] = "sent"
            row["last_provider_message_id"] = args[1]
            row["last_error"] = None
            row["sent_at"] = datetime.now(tz=UTC).isoformat()
        elif "SET status = 'retrying'" in query:
            row["status"] = "retrying"
            row["last_error"] = args[1]
            row["next_attempt_at"] = args[2]
        elif "SET status = 'failed'" in query:
            row["status"] = "failed"
            row["last_error"] = args[1]
        elif "SET status = 'cancelled'" in query:
            row["status"] = "cancelled"
            row["last_error"] = args[1]
        row["updated_at"] = datetime.now(tz=UTC).isoformat()
        self.rows[email_id] = row


@pytest.mark.asyncio
async def test_email_outbox_repository_roundtrip() -> None:
    repo = EmailOutboxRepository(FakePrisma())
    now = datetime.now(tz=UTC)

    stored = await repo.enqueue(
        EmailOutboxRecord(
            email_id="email-1",
            kind="test",
            provider="smtp",
            to_addresses=["user@example.com"],
            cc_addresses=["cc@example.com"],
            bcc_addresses=[],
            from_address="DeltaLLM <noreply@example.com>",
            reply_to="support@example.com",
            template_key="test_email",
            payload_json={"hello": "world"},
            subject="subject",
            text_body="body",
            html_body="<p>body</p>",
            next_attempt_at=now,
            created_by_account_id="acct-1",
        )
    )

    assert stored.email_id == "email-1"
    assert stored.payload_json == {"hello": "world"}
    assert await repo.count_pending() == 1

    claimed = await repo.claim_due(limit=1)
    assert len(claimed) == 1
    assert claimed[0].status == "sending"
    assert claimed[0].attempt_count == 1

    await repo.mark_retry("email-1", error="temporary", next_attempt_at=now)
    retrying = await repo.get_by_email_id("email-1")
    assert retrying is not None
    assert retrying.status == "retrying"
    assert retrying.last_error == "temporary"

    await repo.mark_failed("email-1", error="terminal")
    failed = await repo.get_by_email_id("email-1")
    assert failed is not None
    assert failed.status == "failed"

    await repo.update_recipients_and_payload(
        "email-1",
        to_addresses=["updated@example.com"],
        cc_addresses=[],
        bcc_addresses=[],
        payload_json={"suppressed_recipients": ["cc@example.com"]},
    )
    updated = await repo.get_by_email_id("email-1")
    assert updated is not None
    assert updated.to_addresses == ["updated@example.com"]
    assert updated.payload_json == {"suppressed_recipients": ["cc@example.com"]}

    await repo.cancel("email-1", reason="all recipients are suppressed")
    cancelled = await repo.get_by_email_id("email-1")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.last_error == "all recipients are suppressed"


@pytest.mark.asyncio
async def test_email_outbox_repository_marks_sent() -> None:
    repo = EmailOutboxRepository(FakePrisma())
    await repo.enqueue(
        EmailOutboxRecord(
            email_id="email-2",
            kind="transactional",
            provider="smtp",
            to_addresses=["user@example.com"],
            from_address="DeltaLLM <noreply@example.com>",
            subject="subject",
            text_body="body",
        )
    )

    await repo.mark_sent("email-2", provider_message_id="provider-123")

    stored = await repo.get_by_email_id("email-2")
    assert stored is not None
    assert stored.status == "sent"
    assert stored.last_provider_message_id == "provider-123"
    assert stored.sent_at is not None


@pytest.mark.asyncio
async def test_email_outbox_repository_summary_and_purge_helpers() -> None:
    repo = EmailOutboxRepository(FakePrisma())
    now = datetime.now(tz=UTC)
    old = now.replace(year=2020)
    for email_id, status, updated_at in (
        ("email-1", "sent", old),
        ("email-2", "failed", old),
        ("email-3", "queued", now),
    ):
        await repo.enqueue(
            EmailOutboxRecord(
                email_id=email_id,
                kind="test",
                provider="smtp",
                to_addresses=["user@example.com"],
                from_address="DeltaLLM <noreply@example.com>",
                subject="subject",
                text_body="body",
                status=status,
                next_attempt_at=now,
            )
        )
        repo.prisma.rows[email_id]["status"] = status
        repo.prisma.rows[email_id]["updated_at"] = updated_at

    summary = await repo.summarize_status_counts()
    assert {item.status: item.count for item in summary} == {"failed": 1, "queued": 1, "sent": 1}

    recent = await repo.list_recent(limit=2)
    assert len(recent) == 2

    removed = await repo.purge_terminal_before(before=now)
    assert removed == 2
    assert await repo.get_by_email_id("email-1") is None
    assert await repo.get_by_email_id("email-3") is not None
