import pytest

from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.errors import is_record_specific_database_error
from src.db.spend_ingestion import SpendOutboxRecord
from tests.test_spend_ingestion import _OutboxDB, _Writer, _spend_payload


class DatabaseFailure(Exception):
    def __init__(self, code):
        self.meta = {"code": code}


@pytest.mark.parametrize(
    "code,record_specific", [("PBR01", True), ("40P01", False), ("57014", False), ("08006", False)]
)
def test_receipt_conflict_is_isolated_but_infrastructure_failures_are_not(code, record_specific):
    assert is_record_specific_database_error(DatabaseFailure(code)) is record_specific


class ConflictingWriter(_Writer):
    async def log_prepared_batch_once(self, events):
        if any(event.event_id == "conflict" for event in events):
            raise DatabaseFailure("PBR01")
        return await super().log_prepared_batch_once(events)


async def test_conflicting_receipt_does_not_retry_valid_neighbors():
    db = _OutboxDB()
    service = SpendIngestionService(
        db_client=db, writer=ConflictingWriter(), config=SpendIngestionConfig(enabled=True)
    )
    await service._process_batch(
        [
            SpendOutboxRecord(identity, "spend", _spend_payload(), 1)
            for identity in ("good-before", "conflict", "good-after")
        ]
    )
    completed = [
        event_id
        for query, params in db.executions
        if "SELECT event_id FROM completed" in query
        for event_id in params[0]
    ]
    retried = [
        params[0] for query, params in db.executions if "SELECT event_id FROM transitioned" in query
    ]
    assert completed == ["good-before", "good-after"]
    assert retried == ["conflict"]
