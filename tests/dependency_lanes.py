from __future__ import annotations

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


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del config
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
