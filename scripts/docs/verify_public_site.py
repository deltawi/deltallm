"""Fail when a built documentation artifact contains unpublished sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that an MkDocs artifact contains public content only."
    )
    parser.add_argument("site_dir", type=Path, help="Built MkDocs site directory")
    return parser.parse_args()


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"expected UTF-8 text artifact: {path}") from exc


def verify_public_site(site_dir: Path) -> list[str]:
    """Return publication-containment failures for an MkDocs site directory."""

    failures: list[str] = []
    if not site_dir.is_dir():
        return [f"site directory does not exist: {site_dir}"]

    internal_paths = sorted(
        path.relative_to(site_dir)
        for path in site_dir.rglob("*")
        if "internal" in path.relative_to(site_dir).parts
    )
    if internal_paths:
        rendered = ", ".join(str(path) for path in internal_paths[:10])
        failures.append(f"internal artifact paths were published: {rendered}")

    search_index = site_dir / "search" / "search_index.json"
    if not search_index.is_file():
        failures.append(f"search index is missing: {search_index}")
    else:
        try:
            search_data = json.loads(_load_text(search_index))
        except (json.JSONDecodeError, ValueError) as exc:
            failures.append(f"search index is invalid: {exc}")
        else:
            documents = search_data.get("docs", [])
            leaked_locations = sorted(
                str(document.get("location", ""))
                for document in documents
                if str(document.get("location", "")).lstrip("/").startswith("internal/")
            )
            if leaked_locations:
                rendered = ", ".join(leaked_locations[:10])
                failures.append(f"internal pages were included in the search index: {rendered}")

    sitemap = site_dir / "sitemap.xml"
    if not sitemap.is_file():
        failures.append(f"sitemap is missing: {sitemap}")
    elif "/internal/" in _load_text(sitemap):
        failures.append("internal pages were included in sitemap.xml")

    return failures


def main() -> int:
    failures = verify_public_site(_parse_args().site_dir.resolve())
    if not failures:
        print("Public documentation artifact contains no internal pages.")
        return 0

    for failure in failures:
        print(f"ERROR: {failure}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
