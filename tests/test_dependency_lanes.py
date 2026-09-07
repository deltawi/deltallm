from pathlib import Path

import pytest

from tests.dependency_lanes import DEPENDENCY_LANES
from tests.dependency_lanes import DependencyLaneError
from tests.dependency_lanes import classify_dependency_lane
from tests.dependency_lanes import detect_external_dependency


def test_dependency_lanes_are_stable_and_mutually_exclusive() -> None:
    assert DEPENDENCY_LANES == ("hermetic", "app", "postgres", "redis", "helm")


def test_explicit_external_lane_takes_precedence_over_app_fixture() -> None:
    assert (
        classify_dependency_lane(
            marker_names=["postgres", "asyncio"],
            fixture_names=["test_app"],
            expected_external_lane="postgres",
        )
        == "postgres"
    )


def test_shared_application_fixture_selects_app_lane() -> None:
    assert (
        classify_dependency_lane(
            marker_names=["asyncio"],
            fixture_names=["client", "test_app"],
            expected_external_lane=None,
        )
        == "app"
    )


def test_dependency_free_test_defaults_to_hermetic_lane() -> None:
    assert (
        classify_dependency_lane(
            marker_names=[],
            fixture_names=["tmp_path"],
            expected_external_lane=None,
        )
        == "hermetic"
    )


def test_conflicting_primary_lanes_are_rejected() -> None:
    with pytest.raises(DependencyLaneError, match="multiple dependency lanes"):
        classify_dependency_lane(
            marker_names=["app", "redis"],
            fixture_names=[],
            expected_external_lane="redis",
        )


def test_unmarked_external_dependency_is_rejected() -> None:
    with pytest.raises(DependencyLaneError, match="explicit @pytest.mark.redis"):
        classify_dependency_lane(
            marker_names=[],
            fixture_names=[],
            expected_external_lane="redis",
        )


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        (Path("tests/helm/test_chart.py"), "", "helm"),
        (Path("tests/test_cache.py"), "Redis" + ".from_url(url)", "redis"),
        (Path("tests/test_cache.py"), "Redis" + " . from_url (url)", "redis"),
        (
            Path("tests/test_cache.py"),
            'os.environ["DELTALLM_TEST_' + 'REDIS_URL"]',
            "redis",
        ),
        (Path("tests/test_repository.py"), "await connect_" + "prisma()", "postgres"),
        (
            Path("tests/test_repository.py"),
            "await connect_" + "prisma ()",
            "postgres",
        ),
        (
            Path("tests/test_repository.py"),
            "await _connect_" + "prisma()",
            "postgres",
        ),
        (
            Path("tests/test_repository.py"),
            "Prisma" + "(datasource={'url': url})",
            "postgres",
        ),
        (Path("tests/test_service.py"), "FakeRedis()", None),
    ],
)
def test_external_dependency_detection(
    path: Path,
    source: str,
    expected: str | None,
) -> None:
    assert detect_external_dependency(path=path, source=source) == expected
