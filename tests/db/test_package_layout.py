"""Keep database groups and moved repository owners discoverable."""

import ast
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
import src.db as database
from src.db.audit import repository as audit
from src.db.catalog import model_deployments
from src.db.identity.key_repository import KeyRepository
from src.db.json_fields import _parse_metadata
from src.db.runtime.client import PrismaClientManager
from src.db.runtime.migration_status import MIGRATIONS_DIRECTORY, required_migrations


ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "src" / "db"
GROUPS = {
    "runtime",
    "identity",
    "organizations",
    "catalog",
    "routing",
    "tiers",
    "billing",
    "accounting",
    "audit",
    "email",
    "mcp",
}
ACCOUNTING_GROUPS = {"permits", "journal", "reporting", "health"}


def test_database_root_keeps_only_exports_errors_and_json_field_mapping():
    assert {path.name for path in DATABASE.glob("*.py")} == {
        "__init__.py",
        "errors.py",
        "json_fields.py",
    }
    assert {
        path.name for path in DATABASE.iterdir() if path.is_dir() and path.name != "__pycache__"
    } == GROUPS
    assert {
        path.name
        for path in (DATABASE / "accounting").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    } == ACCOUNTING_GROUPS
    assert (DATABASE / "organizations" / "deletion" / "__init__.py").is_file()


def test_database_subpackages_are_included_in_distribution_discovery():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    discovery = config["tool"]["setuptools"]["packages"]["find"]
    for initializer in DATABASE.rglob("__init__.py"):
        name = ".".join(initializer.parent.relative_to(ROOT).parts)
        assert any(fnmatchcase(name, pattern) for pattern in discovery["include"]), name
        assert not any(fnmatchcase(name, pattern) for pattern in discovery.get("exclude", [])), name


def test_database_public_exports_keep_the_same_repository_owners():
    assert set(database.__all__) == {"PrismaClientManager", "KeyRepository", "AuditRepository"}
    assert database.PrismaClientManager is PrismaClientManager
    assert database.KeyRepository is KeyRepository
    assert database.AuditRepository is audit.AuditRepository


def test_split_repositories_share_one_json_field_mapping_owner():
    assert audit._parse_metadata is _parse_metadata
    assert model_deployments._parse_metadata is _parse_metadata
    assert _parse_metadata.__annotations__ == {
        "value": "Any",
        "return": "dict[str, Any] | None",
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ({"name": "value"}, {"name": "value"}),
        ('{"name": "value"}', {"name": "value"}),
        ("[]", None),
        ("null", None),
        ("invalid JSON", None),
        (42, None),
    ],
)
def test_shared_json_field_mapping_keeps_optional_object_semantics(value, expected):
    assert _parse_metadata(value) == expected


def test_moved_migration_manifest_uses_the_existing_repository_migrations():
    expected = ROOT / "prisma" / "migrations"
    assert MIGRATIONS_DIRECTORY == expected
    files = sorted(expected.glob("*/migration.sql"))
    assert files
    assert required_migrations() == {
        path.parent.name: sha256(path.read_bytes()).hexdigest() for path in files
    }


@pytest.mark.parametrize("directory", ["src", "tests", "scripts"])
def test_python_callers_use_grouped_database_imports(directory):
    allowed = GROUPS | {"errors", "json_fields"}
    root_exports = allowed | set(database.__all__)
    for path in (ROOT / directory).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.module == "src.db":
                    assert all(alias.name in root_exports for alias in node.names), path
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if name.startswith("src.db."):
                    assert name.split(".")[2] in allowed, (path, name)


@pytest.mark.parametrize("reverse", [False, True])
def test_database_modules_import_in_a_fresh_process(reverse):
    code = (
        "import importlib, pkgutil, src.db\n"
        "names = sorted(module.name for module in pkgutil.walk_packages("
        "src.db.__path__, 'src.db.'))\n"
        f"for name in names[::{'-1' if reverse else '1'}]:\n"
        "    importlib.import_module(name)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
