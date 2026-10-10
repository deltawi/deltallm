from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2] / "src"


@pytest.mark.parametrize(
    "module",
    [
        "db/identity/platform_sessions.py",
        "services/identity/platform_session_service.py",
        "services/identity/sso_account_service.py",
        "auth/sso_identity.py",
        "db/identity/platform_passwords.py",
        "services/identity/platform_password_change.py",
        "db/organizations/team_directory.py",
    ],
)
def test_session_and_account_boundaries_have_one_bounded_owner(module: str) -> None:
    source = (ROOT / module).read_text()
    tree = ast.parse(source)
    assert len(source.splitlines()) < 500
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 80, (module, node.name)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("fastapi", "starlette", "src.api"))
        if isinstance(node, ast.Name):
            assert node.id != "Any", module
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and module.startswith("services/")
        ):
            assert "deltallm_platformsession" not in node.value


def test_identity_facade_does_not_reintroduce_session_sql() -> None:
    source = (ROOT / "services/identity/platform_identity_service.py").read_text()
    assert len(source.splitlines()) < 800
    assert "deltallm_platformsession" not in source
