import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
NEW_MODULES = (
    "router/selection/contracts.py",
    "router/selection/parser.py",
    "router/selection/prompt.py",
    "router/selection/provider.py",
    "router/selection/request_state.py",
    "router/selection/service.py",
    "providers/chat_hop.py",
    "providers/chat_upstream.py",
)


@pytest.mark.parametrize("module", NEW_MODULES)
def test_new_boundaries_are_small_typed_and_request_free(module):
    source = (ROOT / "src" / module).read_text()
    assert len(source.splitlines()) < 500
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80, (module, node.name)
        if isinstance(node, ast.Name):
            assert node.id not in {"Any", "Request", "getattr", "hasattr", "setattr"}
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                ("fastapi", "src.db", "redis", "prisma", "src.api", "src.bootstrap")
            )
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            assert name not in {"create_task", "AsyncClient", "Client", "create_pool"}


def test_selector_service_is_not_wired_into_production():
    targets = {
        "src.router.selection.service",
        "src.router.selection.provider",
        "src.router.selection.request_state",
    }
    for path in (ROOT / "src").rglob("*.py"):
        if path.is_relative_to(ROOT / "src/router/selection"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in targets, path
            elif isinstance(node, ast.Import):
                assert not any(alias.name in targets for alias in node.names), path


def test_answer_facade_and_selector_bridge_share_one_signing_and_transport_owner():
    for name in ("src/chat/executor.py", "src/router/selection/provider.py"):
        tree = ast.parse((ROOT / name).read_text())
        assert any(
            isinstance(node, ast.ImportFrom)
            and node.module == "src.providers.chat_hop"
            and any(alias.name == "execute_chat_hop" for alias in node.names)
            for node in ast.walk(tree)
        )
    service = (ROOT / "src/router/selection/service.py").read_text()
    assert "httpx" not in service and "record_router_usage" not in service
