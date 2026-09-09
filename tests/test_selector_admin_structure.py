import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "module",
    [
        "services/selector_evaluation.py",
        "services/selector_evaluation_cli.py",
        "services/routing_cost_reports.py",
    ],
)
def test_selector_admin_services_are_bounded_typed_and_do_not_execute_models(module):
    source = (ROOT / "src" / module).read_text()
    assert len(source.splitlines()) < 500
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80, (module, node.name)
        if isinstance(node, ast.Name):
            assert node.id not in {"Any", "Request", "getattr", "hasattr", "setattr"}
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                (
                    "fastapi",
                    "src.api",
                    "src.providers",
                    "src.chat",
                    "src.router.selection.provider",
                    "src.router.selection.service",
                )
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "create_task",
                "AsyncClient",
                "create_pool",
                "select_once",
            }


def test_selector_admin_components_stay_separate_from_the_route_page():
    folder = ROOT / "ui/src/components/route-groups"
    for name in (
        "PolicySelectorEditor",
        "PolicySelectorSummary",
        "PolicyPublishControl",
        "SelectorEvaluationPanel",
        "SelectorCostPanel",
        "RoutingCostSummary",
        "SelectorTools",
    ):
        assert len((folder / f"{name}.tsx").read_text().splitlines()) < 400
    assert len((ROOT / "ui/src/pages/RouteGroupDetail.tsx").read_text().splitlines()) < 800
