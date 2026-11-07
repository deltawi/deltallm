from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml


CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text())


def _pattern(name: str) -> re.Pattern[str]:
    script = _workflow()["jobs"]["cluster-changes"]["steps"][1]["run"]
    match = re.search(rf"^\s*{name}='([^']+)'$", script, re.MULTILINE)
    assert match, name
    return re.compile(match.group(1))


def _runs_clusters(changed: list[str]) -> bool:
    # Mirrors `grep -qvE`: any line that is not UI or docs runs the clusters.
    ui_or_docs = _pattern("ui_or_docs")
    return any(not ui_or_docs.search(path) for path in changed or [""])


def _runs_migrations(changed: list[str]) -> bool:
    schema = _pattern("schema")
    return any(schema.search(path) for path in changed)


@pytest.mark.parametrize(
    "changed,clusters,migrations",
    [
        (["ui/src/pages/Keys.tsx", "docs/guides/keys.md"], False, False),
        (["README.md", "mkdocs.yml", ".github/workflows/docs.yml"], False, False),
        (["ui/src/a.tsx", "src/main.py"], True, False),
        (["deploy/kubernetes/helm/README.md"], True, False),
        (["src/billing/spend/spend.py"], True, False),
        (["tests/performance/run_capacity_acceptance.py"], True, False),
        (["prisma/migrations/20261107000000_x/migration.sql"], True, True),
        (["src/migration_recovery/recover.sql"], True, True),
        (["scripts/verify_migration_paths.py"], True, True),
        (["uv.lock"], True, True),
        ([], True, False),
    ],
)
def test_change_filter_skips_clusters_only_for_ui_and_docs(changed, clusters, migrations):
    assert _runs_clusters(changed) is clusters
    assert _runs_migrations(changed) is migrations


def test_filtered_jobs_and_required_gate():
    jobs = _workflow()["jobs"]
    for name, output in (
        ("lifecycle-acceptance", "runtime"),
        ("capacity-acceptance", "runtime"),
        ("migration-paths", "migrations"),
    ):
        assert jobs[name]["needs"] == "cluster-changes"
        assert jobs[name]["if"] == f"needs.cluster-changes.outputs.{output} == 'true'"
    gate = jobs["test"]
    assert "cluster-changes" in gate["needs"]
    script = gate["steps"][0]["run"]
    # The filter itself must succeed, so a broken filter cannot skip the clusters.
    assert '"$CHANGES"' in script.split("check_filtered()")[0]
    assert script.count("check_filtered ") == 3
    # Pushes to main always run every job.
    assert 'if [ "$EVENT" != pull_request ]' in jobs["cluster-changes"]["steps"][1]["run"]
