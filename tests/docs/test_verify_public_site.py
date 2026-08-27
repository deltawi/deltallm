from __future__ import annotations

import json
from pathlib import Path

from scripts.docs.verify_public_site import verify_public_site


def _write_site_metadata(site_dir: Path, locations: list[str]) -> None:
    search_dir = site_dir / "search"
    search_dir.mkdir(parents=True)
    search_payload = {"docs": [{"location": location} for location in locations]}
    (search_dir / "search_index.json").write_text(json.dumps(search_payload), encoding="utf-8")
    sitemap_urls = "".join(
        f"<url><loc>https://docs.deltallm.io/{location}</loc></url>" for location in locations
    )
    (site_dir / "sitemap.xml").write_text(f"<urlset>{sitemap_urls}</urlset>", encoding="utf-8")


def test_verify_public_site_accepts_public_artifact(tmp_path: Path) -> None:
    _write_site_metadata(tmp_path, ["", "getting-started/"])

    assert verify_public_site(tmp_path) == []


def test_verify_public_site_rejects_internal_artifacts(tmp_path: Path) -> None:
    _write_site_metadata(tmp_path, ["", "internal/master-prd-outline/"])
    internal_page = tmp_path / "internal" / "master-prd-outline" / "index.html"
    internal_page.parent.mkdir(parents=True)
    internal_page.write_text("internal", encoding="utf-8")

    failures = verify_public_site(tmp_path)

    assert any("artifact paths" in failure for failure in failures)
    assert any("search index" in failure for failure in failures)
    assert any("sitemap.xml" in failure for failure in failures)


def test_verify_public_site_rejects_missing_artifact(tmp_path: Path) -> None:
    missing_site = tmp_path / "missing"

    assert verify_public_site(missing_site) == [f"site directory does not exist: {missing_site}"]
