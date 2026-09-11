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
    "router/selection/lanes.py",
    "router/selection/planning.py",
    "router/selection/request_context.py",
    "router/group_policy.py",
    "router/group_execution.py",
    "router/initial_selection.py",
    "router/static_filters.py",
    "router/selection/activation.py",
    "router/selection/answer_observation.py",
    "router/selection/eligibility.py",
    "router/selection/operation.py",
    "router/selection/qualification.py",
    "router/selection/reachability.py",
    "router/selection/runtime.py",
    "router/selection/target_validation.py",
    "chat_capabilities.py",
    "providers/chat_hop.py",
    "providers/chat_upstream.py",
    "batch/selector_checkpoint.py",
    "batch/selector_identity.py",
    "batch/selector_execution.py",
    "batch/chat_capacity.py",
    "batch/chat_lease_lifecycle.py",
    "batch/public_errors.py",
    "router/attempt_capacity.py",
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


def test_selector_execution_is_owned_by_the_authenticated_edge_and_selection_package():
    targets = {
        "src.router.selection.service",
        "src.router.selection.provider",
        "src.router.selection.request_state",
    }
    for path in (ROOT / "src").rglob("*.py"):
        if path.is_relative_to(ROOT / "src/router/selection"):
            continue
        if path == ROOT / "src/routers/selector_edge.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                if (
                    path == ROOT / "src/batch/selector_execution.py"
                    and node.module == "src.router.selection.request_state"
                ):
                    assert [alias.name for alias in node.names] == ["RequestSelectorState"]
                    continue
                assert node.module not in targets, path
            elif isinstance(node, ast.Import):
                assert not any(alias.name in targets for alias in node.names), path


def test_planning_does_not_own_selector_provider_execution():
    for module in (
        "router/router.py",
        "router/selection/planning.py",
        "router/selection/request_context.py",
    ):
        tree = ast.parse((ROOT / "src" / module).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"invoke", "select_once", "create_task"}, module
    tree = ast.parse((ROOT / "src/router/router.py").read_text())
    assert len((ROOT / "src/router/router.py").read_text().splitlines()) < 800
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {
            "plan_deployments",
            "_plan_eligible_group",
        }:
            assert node.end_lineno - node.lineno < 80


def test_batch_selector_boundaries_do_not_grow_worker_or_reimplement_provider_policy():
    for module in ("batch/selector_edge.py", "batch/repositories/selector_repository.py"):
        source = (ROOT / "src" / module).read_text()
        assert len(source.splitlines()) < 150
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno < 80, (module, node.name)
            if isinstance(node, ast.Name):
                assert node.id not in {"Any", "Request"}
    for module, limit in (
        ("batch/chat_worker_execution.py", 800),
        ("batch/chat_item_execution.py", 400),
        ("batch/chat_dispatch.py", 250),
    ):
        assert len((ROOT / "src" / module).read_text().splitlines()) < limit
    edge = (ROOT / "src/batch/selector_edge.py").read_text()
    assert "preflight.auth_verified" in edge and "factory.require_ready()" in edge
    source = (ROOT / "src/batch/selector_execution.py").read_text()
    assert "SelectorOperation(" in source
    assert "SelectorService(" not in source and "SelectorProviderHop(" not in source


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


def test_selector_execution_requires_authenticated_cache_admission_and_shared_accounting():
    edge = (ROOT / "src/routers/chat.py").read_text()
    assert (
        edge.index("preflight = await run_text_preflight")
        < edge.index("selector_deadline = bind_selector_operation")
        < edge.index("primary = await require_initial_deployment")
    )
    source = (ROOT / "src/router/selection/runtime.py").read_text()
    assert "AccountedSelectorHop(store=self._billing" in source
    assert "ReservedSelectorAdmission(" in source and "CapacityAdmittedSelectorHop(" in source
    bootstrap = (ROOT / "src/bootstrap/selector.py").read_text()
    assert "BillingOperationRecovery(" in bootstrap and "selector_events_only=True" in bootstrap
    edge = (ROOT / "src/routers/selector_edge.py").read_text()
    assert "cache.require_provider_execution()" in edge
    for node in ast.walk(ast.parse(edge)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80, node.name
