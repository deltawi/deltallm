"""Export DeltaLLM's FastAPI schema as a deterministic public artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import warnings

from scripts.docs.generated_artifact import write_or_check
from src.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "api" / "openapi.json"
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the artifact is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _operation_ids(schema: dict[str, object]) -> dict[str, list[str]]:
    locations: dict[str, list[str]] = {}
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI schema has no paths object")

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "").strip()
            if not operation_id:
                raise ValueError(f"OpenAPI operation is missing operationId: {method} {path}")
            locations.setdefault(operation_id, []).append(f"{method.upper()} {path}")
    return locations


def generate_openapi() -> tuple[str, int, int]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = create_app().openapi()

    duplicate_warnings = [
        str(item.message) for item in caught if "Duplicate Operation ID" in str(item.message)
    ]
    if duplicate_warnings:
        raise ValueError("; ".join(duplicate_warnings))

    operation_ids = _operation_ids(schema)
    duplicates = {
        operation_id: locations
        for operation_id, locations in operation_ids.items()
        if len(locations) > 1
    }
    if duplicates:
        rendered = "; ".join(
            f"{operation_id}: {', '.join(locations)}"
            for operation_id, locations in sorted(duplicates.items())
        )
        raise ValueError(f"duplicate OpenAPI operation IDs: {rendered}")

    paths = schema.get("paths")
    path_count = len(paths) if isinstance(paths, dict) else 0
    content = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False)
    return content, path_count, len(operation_ids)


def main() -> int:
    args = _parse_args()
    try:
        content, path_count, operation_count = generate_openapi()
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if not write_or_check(args.output.resolve(), content, check=args.check):
        return 1
    print(f"OpenAPI contains {path_count} paths and {operation_count} operations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
