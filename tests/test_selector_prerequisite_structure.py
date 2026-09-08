import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MODULES = (
    "billing/operation_reservation.py",
    "billing/routing_costs.py",
    "cache/execution_eligibility.py",
    "db/billing_operations.py",
    "db/billing_operation_recovery.py",
    "db/routing_costs.py",
    "db/spend_components.py",
    "metrics/selector.py",
    "providers/token_receipt.py",
    "router/selection/capacity.py",
    "router/selection/economics.py",
    "router/selection/prerequisites.py",
    "telemetry/selector_decision.py",
)


@pytest.mark.parametrize("module", MODULES)
def test_new_prerequisite_boundaries_are_small_typed_and_request_free(module):
    source = (ROOT / "src" / module).read_text()
    assert len(source.splitlines()) < 500
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80, (module, node.name)
        if isinstance(node, ast.Name):
            assert node.id not in {"Any", "Request", "getattr", "hasattr", "setattr"}, module
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("fastapi", "src.api", "src.bootstrap")), (
                module
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"create_task", "create_pool", "AsyncClient", "Client"}, (
                module
            )


def test_response_cache_gate_is_mandatory_in_admitted_execution_factory():
    source = (ROOT / "src/router/selection/prerequisites.py").read_text()
    assert "cache: ResponseCacheEligibility" in source
    assert "ReservedSelectorAdmission" in source
    source = (ROOT / "src/router/selection/economics.py").read_text()
    assert source.index("require_provider_execution()") < source.index("self._store.reserve(")
