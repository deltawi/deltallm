import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2] / "src"


@pytest.mark.parametrize(
    "module",
    [
        "realtime/contracts.py",
        "realtime/admission.py",
        "realtime/capacity.py",
        "realtime/config.py",
        "realtime/controls.py",
        "realtime/routing.py",
        "billing/realtime_charge.py",
        "billing/realtime_pricing.py",
        "db/realtime_billing.py",
        "db/realtime_recovery.py",
        "realtime/errors.py",
        "realtime/lifecycle.py",
        "realtime/protocol.py",
        "realtime/runtime.py",
        "realtime/session.py",
        "providers/openai_realtime.py",
        "billing/realtime_usage.py",
        "api/v1/endpoints/realtime.py",
    ],
)
def test_realtime_modules_stay_bounded_and_transport_has_no_framework_dependency(module):
    source = (ROOT / module).read_text()
    assert len(source.splitlines()) < 500
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80, (module, node.name)
        if isinstance(node, ast.ImportFrom) and not module.startswith("api/"):
            assert not (node.module or "").startswith(("fastapi", "starlette", "src.api"))
