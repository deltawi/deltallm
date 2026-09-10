from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(params=["deepseek", "zai", "qwen", "tencent", "minimax"])
def contract(request: pytest.FixtureRequest) -> dict[str, object]:
    path = Path(__file__).parents[1] / "fixtures" / "providers" / f"{request.param}.json"
    return json.loads(path.read_text())
