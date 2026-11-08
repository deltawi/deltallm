from __future__ import annotations

from pathlib import Path

import yaml


CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_postgres_shards_raise_the_checkpoint_limits_before_tests_run():
    # A shard writes about 1 GB of WAL. With the default max_wal_size the forced
    # checkpoint landed on the 250 ms billing tests and failed them on slow disks.
    steps = yaml.safe_load(CI.read_text())["jobs"]["postgres-tests"]["steps"]
    names = [step.get("name") for step in steps]
    tuning = steps[names.index("Keep checkpoints out of the test run")]["run"]
    assert "max_wal_size = '8GB'" in tuning
    assert "checkpoint_timeout = '30min'" in tuning
    assert "pg_reload_conf()" in tuning
    assert "fsync" not in tuning
    assert names.index("Keep checkpoints out of the test run") < names.index("Run PostgreSQL tests")
