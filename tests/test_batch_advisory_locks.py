from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.batch.scheduling.advisory_locks import (
    advisory_lock_key,
    advisory_lock_mode,
    advisory_lock_legacy_parts,
    advisory_lock_name,
    parse_advisory_lock_bool,
    set_advisory_lock_mode,
)


def test_advisory_lock_name_is_canonical_and_namespaced() -> None:
    assert (
        advisory_lock_name("batch_scheduler_flow", " standard ", " embeddings ")
        == "batch_scheduler_flow:standard:embeddings"
    )
    assert advisory_lock_name("batch_scheduler_flow", "", None) == (
        "batch_scheduler_flow:unknown:unknown"
    )
    assert advisory_lock_name("batch_scheduler_flow", "tier:a", r"model\b") == (
        r"batch_scheduler_flow:tier\:a:model\\b"
    )


def test_advisory_lock_key_is_stable_signed_bigint() -> None:
    key = advisory_lock_key("batch_scheduler_flow", "standard", "embeddings")

    assert key == -1367265820846081066
    assert -(2**63) <= key < 2**63


def test_advisory_lock_key_pins_representative_namespaces() -> None:
    assert advisory_lock_key("batch_model_capacity", "standard", "embeddings") == 674975901626903881
    assert advisory_lock_key("batch_scope", "team", "team-a") == -754794978809207070
    assert (
        advisory_lock_key("batch_scheduler", "scheduler_dimensions_backfill")
        == -1521901356306249610
    )


def test_advisory_lock_key_is_stable_across_processes() -> None:
    script = """
import json
from src.batch.scheduling.advisory_locks import advisory_lock_key
print(json.dumps({
    "flow": advisory_lock_key("batch_scheduler_flow", "standard", "embeddings"),
    "capacity": advisory_lock_key("batch_model_capacity", "standard", "embeddings"),
    "scope": advisory_lock_key("batch_scope", "team", "team-a"),
    "backfill": advisory_lock_key("batch_scheduler", "scheduler_dimensions_backfill"),
}, sort_keys=True))
"""
    output = subprocess.check_output(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )

    assert json.loads(output) == {
        "flow": -1367265820846081066,
        "capacity": 674975901626903881,
        "scope": -754794978809207070,
        "backfill": -1521901356306249610,
    }


def test_advisory_lock_legacy_parts_match_two_arg_postgres_lock_inputs() -> None:
    assert advisory_lock_legacy_parts(" model-a ", " standard ") == ("model-a", "standard")
    assert advisory_lock_legacy_parts("", None) == ("unknown", "unknown")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("t", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("f", False),
        ("0", False),
        ("", False),
        (None, False),
    ],
)
def test_parse_advisory_lock_bool_handles_driver_representations(
    value: object,
    expected: bool,
) -> None:
    assert parse_advisory_lock_bool(value) is expected


def test_advisory_lock_mode_is_validated() -> None:
    try:
        set_advisory_lock_mode("canonical")
        assert advisory_lock_mode() == "canonical"
        set_advisory_lock_mode("dual")
        assert advisory_lock_mode() == "dual"
        with pytest.raises(ValueError, match="dual or canonical"):
            set_advisory_lock_mode("legacy")
    finally:
        set_advisory_lock_mode("dual")


def test_advisory_lock_key_separates_representative_scheduler_pairs() -> None:
    keys = {
        advisory_lock_key("batch_scheduler_flow", "standard", "embeddings"),
        advisory_lock_key("batch_scheduler_flow", "priority", "embeddings"),
        advisory_lock_key("batch_scheduler_flow", "standard", "chat"),
        advisory_lock_key("batch_model_capacity", "standard", "embeddings"),
    }

    assert len(keys) == 4
