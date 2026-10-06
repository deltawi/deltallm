"""Cached fixture images must prove the exact pinned registry digest."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from tests.performance.run_native_qualification import ensure_fixture_image

PIN = "sha256:" + "a" * 64


def image_lookup(inspections):
    calls, events = [], []
    pending = iter(inspections)

    def run(*command, **options):
        calls.append((command, options))
        if command[1:3] == ("image", "inspect"):
            code, output = next(pending)
            return subprocess.CompletedProcess(command, code, output, "")
        assert command[1] == "pull"
        return subprocess.CompletedProcess(command, 0, "", "")

    def event(name, **values):
        events.append((name, values))

    return SimpleNamespace(run=run, event=event), calls, events


@pytest.mark.parametrize("reference", ["postgres", "registry:5000/path/metrics:v1"])
def test_exact_cached_digest_avoids_registry_io(reference):
    source = reference + "@" + PIN
    repository = reference.rsplit("/", 1)[-1].split(":", 1)[0]
    prefix = reference.rpartition("/")[0]
    expected = (prefix + "/" if prefix else "") + repository + "@" + PIN
    cluster, calls, events = image_lookup([(0, json.dumps([expected]))])
    ensure_fixture_image(cluster, source)
    assert len(calls) == 1
    assert calls[0][0][3] == source
    assert calls[0][1] == {"timeout": 30, "check": False}
    assert events == [("fixture_image_verified", {"pinned_source": source, "cache_hit": True})]


def test_missing_image_is_pulled_by_digest_and_verified():
    source = "postgres@" + PIN
    cluster, calls, events = image_lookup([(1, ""), (0, json.dumps([source]))])
    ensure_fixture_image(cluster, source)
    assert calls[1] == (("docker", "pull", source), {"timeout": 300})
    assert calls[2][0][3] == source
    assert events[0][1]["cache_hit"] is False


@pytest.mark.parametrize(
    "metadata", [[], ["postgres@sha256:" + "b" * 64], "postgres", None, [1], ["x"] * 33]
)
def test_invalid_cached_proof_fails_without_using_a_tag(metadata):
    cluster, calls, events = image_lookup([(0, json.dumps(metadata))])
    with pytest.raises(ValueError, match="does not prove"):
        ensure_fixture_image(cluster, "postgres@" + PIN)
    assert len(calls) == 1 and events == []


def test_pulled_image_must_also_prove_the_pinned_digest():
    cluster, calls, events = image_lookup([(1, ""), (0, "[]")])
    with pytest.raises(ValueError, match="does not prove"):
        ensure_fixture_image(cluster, "postgres@" + PIN)
    assert len(calls) == 3 and events == []


@pytest.mark.parametrize("metadata", ["{bad", "x" * 16_385])
def test_invalid_or_oversized_digest_metadata_fails(metadata):
    cluster, calls, events = image_lookup([(0, metadata)])
    with pytest.raises(ValueError):
        ensure_fixture_image(cluster, "postgres@" + PIN)
    assert len(calls) == 1 and events == []


@pytest.mark.parametrize("source", ["postgres:15", "postgres@sha256:bad"])
def test_unpinned_fixture_never_reaches_docker(source):
    cluster, calls, events = image_lookup([])
    with pytest.raises(ValueError, match="pinned digest"):
        ensure_fixture_image(cluster, source)
    assert calls == events == []
