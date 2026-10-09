import ast
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from src.batch.accounting_checkpoint import (
    BatchAccountingCheckpoint,
    BatchAccountingUnavailable,
    BatchAccountingWrite,
)
from src.batch.selector_checkpoint import BatchSelectorClaim
from src.billing.accounting.accounting_protocol import AccountingAttempt
from tests.batch.accounting_fixtures import checkpoint_for, uncertain


@pytest.fixture
def proof():
    claim = BatchSelectorClaim("batch", "item", "key", "worker", 1)
    return claim, checkpoint_for(claim)


def test_proof_round_trip_preserves_exact_dispatch_and_terminal_identity(proof):
    claim, pending = proof
    complete = uncertain(pending)
    recovered = BatchAccountingCheckpoint.model_validate_json(complete.model_dump_json())
    assert recovered == complete
    BatchAccountingWrite(claim, pending, complete).validate()
    BatchAccountingWrite(claim, complete, complete).validate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("batch_id", "other"),
        ("item_id", "other"),
        ("api_key", "other"),
        ("claim_epoch", 2),
    ],
)
def test_claim_subject_mismatch_denies_checkpoint(proof, field, value):
    claim, checkpoint = proof
    with pytest.raises(BatchAccountingUnavailable):
        BatchAccountingWrite(replace(claim, **{field: value}), None, checkpoint).validate()


def test_new_owner_can_only_finalize_old_dispatch_not_repeat_it(proof):
    claim, checkpoint = proof
    current = replace(claim, worker_id="new-worker", claim_epoch=2)
    with pytest.raises(BatchAccountingUnavailable):
        BatchAccountingWrite(
            current,
            checkpoint,
            checkpoint.model_copy(update={"claim_epoch": 2}),
        ).validate()
    BatchAccountingWrite(current, checkpoint, uncertain(checkpoint, claim_epoch=2)).validate()


def test_attempt_change_cannot_replace_old_funding_or_erase_old_attempts(proof):
    claim, checkpoint = proof
    other = AccountingAttempt(
        deployment_id="other",
        provider="openai",
        model="test-model",
        pricing_snapshot={},
    )
    changed = checkpoint.model_copy(
        update={
            "operation": checkpoint.operation.model_copy(update={"attempts": (other,)}),
        }
    )
    with pytest.raises(BatchAccountingUnavailable):
        BatchAccountingWrite(claim, checkpoint, changed).validate()
    appended = checkpoint.model_copy(
        update={
            "operation": checkpoint.operation.model_copy(
                update={
                    "attempts": checkpoint.operation.attempts + (other,),
                }
            ),
        }
    )
    BatchAccountingWrite(claim, checkpoint, appended).validate()


def test_frozen_terminal_cannot_change_after_delivery_attempt(proof):
    claim, checkpoint = proof
    complete = uncertain(checkpoint)
    changed = complete.model_copy(
        update={
            "terminal": complete.terminal.model_copy(update={"uncertainty_reason": "different"}),
        }
    )
    with pytest.raises(BatchAccountingUnavailable):
        BatchAccountingWrite(claim, complete, changed).validate()


def test_terminal_with_foreign_event_or_owner_is_rejected(proof):
    _, checkpoint = proof
    complete = uncertain(checkpoint)
    for field in ("event_id", "owner_token", "operation_id"):
        value = complete.model_dump()
        value["terminal"][field] = uuid4()
        with pytest.raises(ValueError, match="Invalid batch terminal proof"):
            BatchAccountingCheckpoint.model_validate(value)


def test_new_batch_modules_have_bounded_typed_interfaces():
    for filename in (
        "accounting_checkpoint.py",
        "repositories/accounting_repository.py",
        "accounting_native.py",
        "accounting_execution.py",
        "accounting_delivery.py",
        "accounting_provider_execution.py",
        "embedding_group_lifecycle.py",
        "repositories/completion_outbox_fences.py",
    ):
        path = Path("src/batch") / filename
        source = path.read_text()
        assert len(source.splitlines()) < 500
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 80, node.name
            if isinstance(node, ast.Name):
                assert node.id not in {"Any", "getattr", "hasattr"}
        assert "app.state" not in source
        assert "create_task" not in source
