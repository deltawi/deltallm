"""Keep service groups, public exports, and commands compatible."""

import ast
from fnmatch import fnmatchcase
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
import src.services as services
from src.services.admission.limit_counter import LimitCounter
from src.services.audit.audit_retention import AuditRetentionConfig, AuditRetentionWorker
from src.services.audit.audit_service import AuditService
from src.services.identity.keys.key_service import KeyService
from src.services.identity.platform_identity_service import PlatformIdentityService
from src.services.identity.self_registration_provisioning import SelfRegistrationProvisioningService
from src.services.models.model_deployments import (
    bootstrap_model_deployments_from_config,
    load_model_registry,
)
from src.services.routing import selector_evaluation_cli


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src/services"
GROUPS = {
    "identity",
    "access",
    "admission",
    "tiers",
    "models",
    "routing",
    "prompts",
    "organizations",
    "email",
    "audit",
    "invalidation",
    "reporting",
    "ui",
}


def test_service_root_keeps_only_public_exports_and_the_documented_command():
    assert {path.name for path in SERVICES.glob("*.py")} == {
        "__init__.py",
        "selector_evaluation_cli.py",
    }
    assert {
        path.name for path in SERVICES.iterdir() if path.is_dir() and path.name != "__pycache__"
    } == GROUPS
    for group in ("identity/keys", "identity/external", "organizations/deletion"):
        assert (SERVICES / group / "__init__.py").is_file()


def test_service_subpackages_are_included_in_distribution_discovery():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    discovery = config["tool"]["setuptools"]["packages"]["find"]
    for initializer in SERVICES.rglob("__init__.py"):
        name = ".".join(initializer.parent.relative_to(ROOT).parts)
        assert any(fnmatchcase(name, pattern) for pattern in discovery["include"]), name
        assert not any(fnmatchcase(name, pattern) for pattern in discovery.get("exclude", [])), name


def test_service_subpackage_initializers_do_not_load_dependencies():
    for initializer in SERVICES.rglob("__init__.py"):
        if initializer.parent == SERVICES:
            continue
        tree = ast.parse(initializer.read_text())
        assert len(tree.body) == 1, initializer
        node = tree.body[0]
        assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant), initializer
        assert isinstance(node.value.value, str), initializer


def test_services_do_not_import_api_or_bootstrap_modules():
    for path in SERVICES.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            assert not any(
                name.startswith(("src.api", "src.bootstrap", "src.main")) for name in names
            ), path


def test_service_public_exports_keep_the_same_owners():
    expected = {
        "AuditRetentionConfig": AuditRetentionConfig,
        "AuditRetentionWorker": AuditRetentionWorker,
        "AuditService": AuditService,
        "KeyService": KeyService,
        "LimitCounter": LimitCounter,
        "PlatformIdentityService": PlatformIdentityService,
        "SelfRegistrationProvisioningService": SelfRegistrationProvisioningService,
        "load_model_registry": load_model_registry,
        "bootstrap_model_deployments_from_config": bootstrap_model_deployments_from_config,
    }
    assert set(services.__all__) == set(expected)
    for name, owner in expected.items():
        assert getattr(services, name) is owner, name


@pytest.mark.parametrize("directory", ["src", "tests", "scripts"])
def test_python_callers_use_grouped_service_imports(directory):
    allowed = GROUPS | {"selector_evaluation_cli"}
    root_exports = allowed | set(services.__all__)
    for path in (ROOT / directory).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.module == "src.services":
                    assert all(alias.name in root_exports for alias in node.names), path
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if name.startswith("src.services."):
                    assert name.split(".")[2] in allowed, (path, name)


@pytest.mark.parametrize("reverse", [False, True])
def test_service_modules_import_in_a_fresh_process(reverse):
    code = (
        "import importlib, pkgutil, src.services\n"
        "names = sorted(module.name for module in pkgutil.walk_packages("
        "src.services.__path__, 'src.services.'))\n"
        f"for name in names[::{'-1' if reverse else '1'}]:\n"
        "    importlib.import_module(name)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_documented_selector_command_has_one_implementation():
    from src.services import selector_evaluation_cli as compatible

    assert compatible.main is selector_evaluation_cli.main


def test_documented_selector_command_keeps_the_same_help():
    outputs = []
    for module in (
        "src.services.selector_evaluation_cli",
        "src.services.routing.selector_evaluation_cli",
    ):
        result = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
