"""Keep billing groups discoverable without restoring flat module imports."""

import ast
from fnmatch import fnmatchcase
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
import src.billing as billing
from src.billing.pricing import cost


ROOT = Path(__file__).resolve().parents[1]
BILLING = ROOT / "src" / "billing"
GROUPS = {"pricing", "budgets", "charges", "spend", "accounting"}
ACCOUNTING_GROUPS = {"permits", "journal", "reporting", "transport", "health"}


def test_billing_root_keeps_only_shared_money_and_public_exports():
    assert {path.name for path in BILLING.glob("*.py")} == {"__init__.py", "money.py"}
    assert {
        path.name for path in BILLING.iterdir() if path.is_dir() and path.name != "__pycache__"
    } == GROUPS
    accounting = BILLING / "accounting"
    assert {
        path.name for path in accounting.iterdir() if path.is_dir() and path.name != "__pycache__"
    } == ACCOUNTING_GROUPS


def test_billing_subpackages_are_included_in_distribution_discovery():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    discovery = config["tool"]["setuptools"]["packages"]["find"]
    for initializer in BILLING.rglob("__init__.py"):
        name = ".".join(initializer.parent.relative_to(ROOT).parts)
        assert any(fnmatchcase(name, pattern) for pattern in discovery["include"]), name
        assert not any(fnmatchcase(name, pattern) for pattern in discovery.get("exclude", [])), name


def test_billing_public_cost_exports_keep_the_same_owners():
    assert set(billing.__all__) == {
        "ModelPricing",
        "DEFAULT_MODEL_COST_MAP",
        "get_model_pricing",
        "completion_cost",
    }
    for name in billing.__all__:
        assert getattr(billing, name) is getattr(cost, name), name


@pytest.mark.parametrize("directory", ["src", "tests", "scripts"])
def test_python_callers_use_grouped_billing_imports(directory):
    allowed = GROUPS | {"money"}
    root_exports = allowed | set(billing.__all__)
    for path in (ROOT / directory).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.module == "src.billing":
                    assert all(alias.name in root_exports for alias in node.names), path
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if name.startswith("src.billing."):
                    assert name.split(".")[2] in allowed, (path, name)


@pytest.mark.parametrize("reverse", [False, True])
def test_billing_modules_import_in_a_fresh_process(reverse):
    code = (
        "import importlib, pkgutil, src.billing\n"
        "names = sorted(module.name for module in pkgutil.walk_packages("
        "src.billing.__path__, 'src.billing.'))\n"
        f"for name in names[::{'-1' if reverse else '1'}]:\n"
        "    importlib.import_module(name)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
