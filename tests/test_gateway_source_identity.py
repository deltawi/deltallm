"""An image cannot match a checkout with different generated-client inputs."""

import subprocess
import sys

import pytest

from tests.performance.gateway_source_identity import (
    RUNTIME_INPUTS,
    image_identity_program,
    source_sha256,
)


@pytest.fixture
def source_tree(tmp_path):
    for name in ("src/app.py", *RUNTIME_INPUTS):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    return tmp_path


@pytest.mark.parametrize("name", ("src/app.py", *RUNTIME_INPUTS))
def test_identity_changes_with_each_runtime_or_generator_input(source_tree, name):
    original = source_sha256(source_tree)
    (source_tree / name).write_text("changed")
    assert source_sha256(source_tree) != original


def test_checkout_and_image_program_use_the_same_identity(source_tree):
    result = subprocess.run(
        [sys.executable, "-c", image_identity_program(source_tree)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.stdout.strip() == source_sha256(source_tree)
