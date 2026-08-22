from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

from src.main import create_app


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "public_route_inventory.json"


def _load_inventory() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _route_methods(route: APIRoute) -> set[str]:
    return {method for method in route.methods if method not in {"HEAD", "OPTIONS"}}


def test_every_runtime_route_has_an_audited_audience_classification() -> None:
    inventory = _load_inventory()
    app = create_app()
    developer_routes = {
        (route["method"], route["path"]): route for route in inventory["developer_routes"]
    }
    family_prefixes = sorted(
        (family["prefix"] for family in inventory["non_developer_route_families"]),
        key=len,
        reverse=True,
    )

    assert len(app.routes) == inventory["baseline_route_count"]
    assert len(developer_routes) == len(inventory["developer_routes"])

    uncovered: list[tuple[list[str], str]] = []
    actual_developer_routes: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = _route_methods(route)
        for method in methods:
            key = (method, route.path)
            if key in developer_routes:
                actual_developer_routes.add(key)
                continue
            if any(route.path.startswith(prefix) for prefix in family_prefixes):
                continue
            uncovered.append((sorted(methods), route.path))

    assert uncovered == []
    assert actual_developer_routes == set(developer_routes)


def test_every_v1_and_mcp_route_is_explicitly_classified() -> None:
    inventory = _load_inventory()
    app = create_app()
    expected = {(route["method"], route["path"]) for route in inventory["developer_routes"]}
    actual = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and (route.path.startswith("/v1/") or route.path == "/mcp")
        for method in _route_methods(route)
    }

    assert actual == expected


def test_developer_route_contract_metadata_is_bounded() -> None:
    inventory = _load_inventory()
    assert {route["audience"] for route in inventory["developer_routes"]} == {"developer"}
    assert {route["dialect"] for route in inventory["developer_routes"]} <= {
        "openai",
        "anthropic",
        "delta_json",
        "json_rpc",
    }
    assert {route["authentication"] for route in inventory["developer_routes"]} == {"bearer"}
