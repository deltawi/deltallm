from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.main import create_app


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "openapi_known_gaps.json"
FILES_BATCHES_OPERATIONS = (
    ("/v1/files", "post"),
    ("/v1/files/{file_id}", "get"),
    ("/v1/files/{file_id}/content", "get"),
    ("/v1/batches", "post"),
    ("/v1/batches", "get"),
    ("/v1/batches/{batch_id}", "get"),
    ("/v1/batches/{batch_id}/cancel", "post"),
)


def _load_known_gaps() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _resolve_schema(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not reference:
        return schema
    prefix = "#/components/schemas/"
    assert reference.startswith(prefix)
    return document["components"]["schemas"][reference.removeprefix(prefix)]


def _detected_gap_ids(document: dict[str, Any]) -> set[str]:
    gaps: set[str] = set()
    paths = document["paths"]

    security_schemes = document.get("components", {}).get("securitySchemes", {})
    auth_parameters = [
        parameter
        for path, method in FILES_BATCHES_OPERATIONS
        for parameter in paths[path][method].get("parameters", [])
        if parameter.get("name") == "Authorization" and parameter.get("in") == "header"
    ]
    if (
        "BearerAuth" not in security_schemes
        and auth_parameters
        and all(parameter.get("required") is False for parameter in auth_parameters)
    ):
        gaps.add("auth_is_optional_header_without_bearer_scheme")

    batch_create_schema = paths["/v1/batches"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    if batch_create_schema == {
        "additionalProperties": True,
        "title": "Payload",
        "type": "object",
    }:
        gaps.add("batch_create_body_is_untyped")

    batch_list = paths["/v1/batches"]["get"]
    batch_list_parameters = {parameter["name"] for parameter in batch_list.get("parameters", [])}
    if "after" not in batch_list_parameters:
        gaps.add("batch_cursor_contract_is_missing")

    file_content_types = set(
        paths["/v1/files/{file_id}/content"]["get"]["responses"]["200"]["content"]
    )
    if file_content_types == {"application/json"}:
        gaps.add("file_content_is_documented_as_json")

    if "get" not in paths["/v1/files"] and "delete" not in paths["/v1/files/{file_id}"]:
        gaps.add("file_list_and_delete_are_missing")

    file_create = paths["/v1/files"]["post"]
    purpose_parameters = [
        parameter
        for parameter in file_create.get("parameters", [])
        if parameter.get("name") == "purpose"
    ]
    multipart_schema = _resolve_schema(
        document,
        file_create["requestBody"]["content"]["multipart/form-data"]["schema"],
    )
    if purpose_parameters == [
        {
            "in": "query",
            "name": "purpose",
            "required": False,
            "schema": {
                "default": "batch",
                "title": "Purpose",
                "type": "string",
            },
        }
    ] and "purpose" not in multipart_schema.get("properties", {}):
        gaps.add("file_purpose_is_query_not_multipart")

    success_schemas = [
        paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        for path, method in FILES_BATCHES_OPERATIONS
    ]
    if success_schemas and all(schema == {} for schema in success_schemas):
        gaps.add("files_and_batches_success_schemas_are_empty")

    if any(
        path.startswith(("/auth", "/ui/api", "/health", "/metrics", "/spend", "/global"))
        for path in paths
    ):
        gaps.add("full_schema_mixes_developer_and_private_routes")

    if all("422" in paths[path][method]["responses"] for path, method in FILES_BATCHES_OPERATIONS):
        gaps.add("validation_errors_are_fastapi_422")

    return gaps


def test_current_public_openapi_gaps_are_explicit_and_complete() -> None:
    fixture = _load_known_gaps()
    document = create_app().openapi()
    expected = {gap["id"] for gap in fixture["gaps"]}

    assert _detected_gap_ids(document) == expected


def test_every_known_openapi_gap_names_its_delivery_slice() -> None:
    fixture = _load_known_gaps()
    gaps = fixture["gaps"]

    assert len({gap["id"] for gap in gaps}) == len(gaps)
    assert all(gap["summary"].endswith(".") for gap in gaps)
    assert all(gap["target_slice"] in {1, 2, 3, 4, 5} for gap in gaps)
