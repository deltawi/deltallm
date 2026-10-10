from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import pytest


DEPENDENCY_LANES = ("hermetic", "app", "postgres", "redis", "helm")
EXTERNAL_DEPENDENCY_LANES = frozenset({"postgres", "redis", "helm"})
_APP_FIXTURE = "test_app"
_EXTERNAL_USAGE_PATTERNS = {
    "postgres": (
        re.compile(r"\b_?connect_prisma\s*\("),
        re.compile(r"\bPrisma\s*\(\s*datasource\s*="),
    ),
    "redis": (
        re.compile(r"\bRedis\s*\.\s*from_url\s*\("),
        re.compile(r"[\"']DELTALLM_TEST_REDIS_URL[\"']"),
    ),
}


class DependencyLaneError(ValueError):
    """Raised when a test cannot be assigned to one dependency lane safely."""


def parse_postgres_shard(value: str) -> tuple[int, int]:
    try:
        index, count = (int(part) for part in value.split("/"))
    except ValueError as exc:
        raise ValueError("PostgreSQL shard must be INDEX/COUNT") from exc
    if not 0 <= index < count <= 8:
        raise ValueError("PostgreSQL shard requires 0 <= INDEX < COUNT <= 8")
    return index, count


def postgres_test_shard(nodeid: str, count: int) -> int:
    if not 1 <= count <= 8:
        raise ValueError("PostgreSQL shard count must be between 1 and 8")
    # Keep a module and its scoped fixtures together on one isolated database.
    module = nodeid.split("::", 1)[0]
    return int.from_bytes(hashlib.sha256(module.encode()).digest(), "big") % count


def classify_dependency_lane(
    *,
    marker_names: Iterable[str],
    fixture_names: Iterable[str],
    expected_external_lane: str | None,
) -> str:
    declared_lanes = set(marker_names).intersection(DEPENDENCY_LANES)
    if len(declared_lanes) > 1:
        rendered = ", ".join(sorted(declared_lanes))
        raise DependencyLaneError(f"multiple dependency lanes declared: {rendered}")

    declared_lane = next(iter(declared_lanes), None)
    if expected_external_lane is not None:
        if declared_lane is None:
            raise DependencyLaneError(
                f"real {expected_external_lane} usage requires an explicit "
                f"@pytest.mark.{expected_external_lane} marker"
            )
        if declared_lane != expected_external_lane:
            raise DependencyLaneError(
                f"real {expected_external_lane} usage is marked as {declared_lane}"
            )

    if declared_lane is not None:
        return declared_lane
    if _APP_FIXTURE in fixture_names:
        return "app"
    return "hermetic"


def detect_external_dependency(*, path: Path, source: str) -> str | None:
    detected: set[str] = set()
    normalized_parts = path.as_posix().split("/")
    if "tests" in normalized_parts and "helm" in normalized_parts:
        detected.add("helm")
    detected.update(
        lane
        for lane, patterns in _EXTERNAL_USAGE_PATTERNS.items()
        if any(pattern.search(source) for pattern in patterns)
    )

    # The PostgreSQL lane provisions Redis for cross-owner runtime integration.
    # A test still has one primary lane; the Redis-only lane stays independent.
    if detected == {"postgres", "redis"}:
        return "postgres"
    if len(detected) > 1:
        rendered = ", ".join(sorted(detected))
        raise DependencyLaneError(
            f"test module uses multiple external dependencies ({rendered}); "
            "split the module or extend the lane model deliberately"
        )
    return next(iter(detected), None)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("test dependency lanes")
    group.addoption(
        "--dependency-lane-report",
        action="store_true",
        help="report collected test counts for each dependency lane",
    )
    group.addoption(
        "--postgres-shard",
        type=parse_postgres_shard,
        default=None,
        help="select INDEX/COUNT of PostgreSQL modules; requires -m postgres and isolated services",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    shard = config.getoption("--postgres-shard")
    if shard is not None and config.option.markexpr.strip() != "postgres":
        raise pytest.UsageError("--postgres-shard requires the exact -m postgres selection")
    expected_by_path: dict[Path, str | None] = {}
    errors: list[str] = []

    for item in items:
        path = Path(item.path)
        if path not in expected_by_path:
            try:
                expected_by_path[path] = detect_external_dependency(
                    path=path,
                    source=path.read_text(encoding="utf-8"),
                )
            except (DependencyLaneError, OSError) as exc:
                errors.append(f"{item.nodeid}: {exc}")
                continue

        try:
            lane = classify_dependency_lane(
                marker_names=(marker.name for marker in item.iter_markers()),
                fixture_names=item.fixturenames,
                expected_external_lane=expected_by_path[path],
            )
        except DependencyLaneError as exc:
            errors.append(f"{item.nodeid}: {exc}")
            continue

        if item.get_closest_marker(lane) is None:
            item.add_marker(lane)
        if lane in EXTERNAL_DEPENDENCY_LANES and item.get_closest_marker("integration") is None:
            item.add_marker("integration")

    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise pytest.UsageError(f"invalid test dependency classification:\n{details}")

    if shard is not None:
        index, count = shard
        selected: list[pytest.Item] = []
        deselected: list[pytest.Item] = []
        for item in items:
            if (
                item.get_closest_marker("postgres") is not None
                and postgres_test_shard(item.nodeid, count) == index
            ):
                selected.append(item)
            else:
                deselected.append(item)
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


def pytest_collection_finish(session: pytest.Session) -> None:
    if not session.config.getoption("--dependency-lane-report"):
        return

    counts = Counter(
        lane
        for item in session.items
        for lane in DEPENDENCY_LANES
        if item.get_closest_marker(lane) is not None
    )
    terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal_reporter is None:
        return

    terminal_reporter.write_sep("=", "test dependency lanes")
    for lane in DEPENDENCY_LANES:
        terminal_reporter.write_line(f"{lane:>10}: {counts[lane]}")
    terminal_reporter.write_line(f"{'total':>10}: {sum(counts.values())}")
