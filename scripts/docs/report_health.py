from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
MKDOCS_CONFIG = ROOT / "mkdocs.yml"
IMAGE_PATTERN = re.compile(r"!\[[^\]]*]\(([^)\s]+)(?:\s+[^)]*)?\)")


def _nav_paths(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value.endswith(".md") else set()
    if isinstance(value, list):
        paths: set[str] = set()
        for item in value:
            paths.update(_nav_paths(item))
        return paths
    if isinstance(value, dict):
        paths: set[str] = set()
        for item in value.values():
            paths.update(_nav_paths(item))
        return paths
    return set()


def collect_health() -> dict[str, Any]:
    config = yaml.safe_load(MKDOCS_CONFIG.read_text(encoding="utf-8"))
    nav_paths = _nav_paths(config.get("nav", []))
    pages = sorted(
        path.relative_to(DOCS_DIR).as_posix()
        for path in DOCS_DIR.rglob("*.md")
        if "internal" not in path.relative_to(DOCS_DIR).parts
    )

    missing_h1: list[str] = []
    oversized: list[dict[str, int | str]] = []
    referenced_images: set[str] = set()
    missing_images: list[str] = []

    for relative in pages:
        source = DOCS_DIR / relative
        text = source.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not any(line.startswith("# ") for line in lines):
            missing_h1.append(relative)
        if len(lines) > 500:
            oversized.append({"path": relative, "lines": len(lines)})
        for match in IMAGE_PATTERN.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if "://" in target or target.startswith("data:"):
                continue
            resolved = (source.parent / target).resolve()
            try:
                image_relative = resolved.relative_to(DOCS_DIR.resolve()).as_posix()
            except ValueError:
                continue
            referenced_images.add(image_relative)
            if not resolved.is_file():
                missing_images.append(f"{relative}: {target}")

    image_files = {
        path.relative_to(DOCS_DIR).as_posix()
        for path in DOCS_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
    }

    return {
        "public_markdown_pages": len(pages),
        "navigation_markdown_paths": len(nav_paths),
        "unnavigated_pages": sorted(set(pages) - nav_paths),
        "missing_h1": sorted(missing_h1),
        "referenced_images": len(referenced_images),
        "missing_images": sorted(missing_images),
        "orphan_images": sorted(image_files - referenced_images),
        "oversized_pages_over_500_lines": sorted(oversized, key=lambda item: str(item["path"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report structural health metrics for public docs."
    )
    parser.add_argument(
        "--check", action="store_true", help="Fail on publication-blocking defects."
    )
    args = parser.parse_args()
    report = collect_health()
    print(json.dumps(report, indent=2, sort_keys=True))
    blocking = report["unnavigated_pages"] or report["missing_h1"] or report["missing_images"]
    return 1 if args.check and blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
