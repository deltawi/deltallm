"""Keep the restored runtime owners small and explicitly typed."""

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
MODULES = (
    "billing/accounting_lane_group.py",
    "router/success_completion.py",
    "runtime_logging.py",
    "bootstrap/metrics.py",
    "bootstrap/readiness.py",
    "redis_runtime.py",
)


@pytest.mark.parametrize("module", MODULES)
def test_restored_runtime_owners_keep_module_function_and_type_bounds(module):
    source = (ROOT / "src" / module).read_text()
    assert len(source.splitlines()) < 500, module
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80, (module, node.name)
        if isinstance(node, ast.Name):
            assert node.id != "Any", module
            # These owners retain startup and Starlette/driver adapters.
            if module not in {"redis_runtime.py", "bootstrap/readiness.py"}:
                assert node.id not in {"getattr", "hasattr", "setattr"}, module
