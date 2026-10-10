"""Keep package folder maps complete and explain each folder."""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
FOLDER_LINE = re.compile(r"^([ │├└─]*)([a-z_]+/)\s+#\s+(\S.*)$")


@pytest.mark.parametrize("package", ["billing", "db"])
def test_package_readme_describes_every_subpackage(package):
    directory = ROOT / "src" / package
    readme = (directory / "README.md").read_text()
    tree = readme.split("```text\n", 1)[1].split("```", 1)[0]
    assert tree.splitlines()[0].startswith(f"src/{package}/")
    assert " # " in tree.splitlines()[0]
    described = set()
    parents = []
    for line in tree.splitlines()[1:]:
        if not line.partition("#")[0].rstrip().endswith("/"):
            continue
        match = FOLDER_LINE.fullmatch(line)
        assert match is not None, line
        prefix, folder, description = match.groups()
        assert description.strip(), line
        depth = len(prefix) // 4 - 1
        assert 0 <= depth <= len(parents), line
        parents = parents[:depth] + [folder.rstrip("/")]
        described.add("/".join(parents))
    actual = {
        path.parent.relative_to(directory).as_posix()
        for path in directory.rglob("__init__.py")
        if path.parent != directory
    }
    assert described == actual
