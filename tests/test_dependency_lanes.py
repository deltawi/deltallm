from pathlib import Path

import pytest

from tests.dependency_lanes import DEPENDENCY_LANES
from tests.dependency_lanes import DependencyLaneError
from tests.dependency_lanes import classify_dependency_lane
from tests.dependency_lanes import detect_external_dependency
from tests.dependency_lanes import parse_postgres_shard
from tests.dependency_lanes import postgres_test_shard

pytest_plugins = ("pytester",)


@pytest.mark.parametrize("value", ["", "0", "0/2/3", "x/2", "-1/2", "2/2", "0/0", "0/9"])
def test_postgres_shards_reject_invalid_allocations(value):
    with pytest.raises(ValueError, match="PostgreSQL shard"):
        parse_postgres_shard(value)


def test_postgres_shards_keep_all_parameters_and_scoped_fixtures_together():
    assert parse_postgres_shard("0/2") == (0, 2)
    assert parse_postgres_shard("7/8") == (7, 8)
    for count in (1, 2, 8):
        for module in range(100):
            prefix = f"tests/test_module_{module}.py::test_case"
            assigned = postgres_test_shard(prefix + "[one]", count)
            assert 0 <= assigned < count
            assert postgres_test_shard(prefix + "[two]", count) == assigned
            assert postgres_test_shard(prefix, count) == assigned
    for invalid in (0, 9):
        with pytest.raises(ValueError, match="shard count"):
            postgres_test_shard("tests/test_case.py::test_case", invalid)


def test_postgres_shards_execute_every_case_once_and_preserve_failure(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    pytester.makeini("[pytest]\nmarkers = postgres: synthetic database selection cases")
    modules = {}
    for number in range(8):
        modules[f"test_module_{number}"] = f"""
        from pathlib import Path
        import pytest

        @pytest.fixture(scope="module")
        def owner():
            Path("owner-{number}").touch(exist_ok=False)

        @pytest.mark.postgres
        @pytest.mark.parametrize("case", [0, 1])
        def test_case(case, owner):
            Path(f"case-{number}-{{case}}").touch(exist_ok=False)
            assert ({number}, case) != (0, 1), "expected failure must reach the CI gate"
        """
    pytester.makepyfile(**modules)
    for index in range(2):
        module_count = sum(
            postgres_test_shard(f"{name}.py::test_case", 2) == index for name in modules
        )
        failed = int(postgres_test_shard("test_module_0.py::test_case", 2) == index)
        result = pytester.runpytest_subprocess(
            "-p",
            "tests.dependency_lanes",
            "-q",
            "-m",
            "postgres",
            f"--postgres-shard={index}/2",
            timeout=30,
        )
        result.assert_outcomes(passed=module_count * 2 - failed, failed=failed)
        assert result.ret == (pytest.ExitCode.TESTS_FAILED if failed else pytest.ExitCode.OK)
    assert len(list(pytester.path.glob("owner-*"))) == 8
    assert len(list(pytester.path.glob("case-*"))) == 16


@pytest.mark.parametrize("selection", ["", "hermetic", "postgres or app"])
def test_postgres_shards_cannot_silently_filter_other_lanes(pytester, monkeypatch, selection):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    pytester.makepyfile("def test_case(): pass")
    result = pytester.runpytest_subprocess(
        "-p",
        "tests.dependency_lanes",
        "-q",
        "-m",
        selection,
        "--postgres-shard=0/2",
        timeout=30,
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--postgres-shard requires the exact -m postgres selection*"])


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


def test_postgres_lane_owns_combined_database_and_redis_runtime_tests():
    source = "await _connect_" + "prisma(); Redis" + ".from_url(url)"
    assert (
        detect_external_dependency(path=Path("tests/test_runtime.py"), source=source) == "postgres"
    )
