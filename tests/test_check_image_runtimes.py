from datetime import date

from scripts.ci.check_image_runtimes import (
    node_security_releases_after,
    python_security_releases_after,
)

NODE_INDEX = [
    {"version": "v26.0.0-alpha", "date": "2026-11-01", "security": True},
    {"version": "v24.9.0", "date": "2026-10-20", "security": True},
    {"version": "v22.23.5", "date": "2026-11-11", "security": True},
    {"version": "v22.23.3", "date": "2026-09-23", "security": False},
    {"version": "v22.16.0", "date": "2025-05-21", "security": False},
]
PYTHON_RELEASES = [
    {"name": "Python 3.11.18", "release_date": "2026-10-25T00:00:00Z", "pre_release": False},
    {"name": "Python 3.11.13", "release_date": "2025-06-03T00:00:00Z", "pre_release": False},
    {"name": "Python 3.12.20", "release_date": "2026-10-25T00:00:00Z", "pre_release": False},
    {"name": "Python 3.11.19rc1", "release_date": "2026-11-10T00:00:00Z", "pre_release": True},
]


def test_node_only_counts_newer_security_releases_in_the_same_major() -> None:
    behind = node_security_releases_after("22.16.0", NODE_INDEX)
    assert [(item.fixed_in, item.released) for item in behind] == [("22.23.5", date(2026, 11, 11))]
    assert node_security_releases_after("22.23.5", NODE_INDEX) == []


def test_python_flags_newer_releases_only_on_a_security_only_branch() -> None:
    cycle = {"3.11": {"status": "security"}, "3.12": {"status": "bugfix"}}
    behind = python_security_releases_after("3.11.13", cycle, PYTHON_RELEASES)
    assert [item.fixed_in for item in behind] == ["3.11.18"]
    assert python_security_releases_after("3.11.18", cycle, PYTHON_RELEASES) == []
    assert python_security_releases_after("3.12.1", cycle, PYTHON_RELEASES) == []
