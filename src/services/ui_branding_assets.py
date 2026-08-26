from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast
from xml.etree import ElementTree

from src.config import AppConfig
from src.db.ui_branding_assets import (
    BrandingAssetDatabase,
    UIBrandingAssetKind,
    UIBrandingAssetRepository,
    UI_BRANDING_ASSET_KEYS,
)

logger = logging.getLogger(__name__)

BRANDING_ASSET_KINDS = frozenset(UI_BRANDING_ASSET_KEYS)
BRANDING_ASSET_MAX_BYTES = 2 * 1024 * 1024
BRANDING_ASSET_URL_PREFIX = "/ui/api/branding/assets"

_ASSET_URL_FIELDS: dict[UIBrandingAssetKind, str] = {
    "logo_mark": "logo_mark_url",
    "logo_full": "logo_full_url",
    "favicon": "favicon_url",
}
_ASSET_URL_INDEX: dict[UIBrandingAssetKind, int] = {"logo_mark": 0, "logo_full": 1, "favicon": 2}
_DISALLOWED_SVG_ELEMENTS = frozenset(
    {"script", "foreignobject", "iframe", "object", "embed", "audio", "video", "style"}
)
_SVG_URL_PATTERN = re.compile(r"url\((.*?)\)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class UIBrandingAsset:
    asset_key: UIBrandingAssetKind
    content_type: str
    content: bytes
    content_sha256: str
    size_bytes: int
    original_filename: str | None


def normalize_asset_kind(value: str) -> UIBrandingAssetKind:
    if value not in BRANDING_ASSET_KINDS:
        raise ValueError("unknown branding asset")
    return cast(UIBrandingAssetKind, value)


def branding_asset_url(asset_key: UIBrandingAssetKind, content_sha256: str) -> str:
    return f"{BRANDING_ASSET_URL_PREFIX}/{asset_key}?v={content_sha256}"


def branding_asset_config_field(asset_key: UIBrandingAssetKind) -> str:
    return _ASSET_URL_FIELDS[asset_key]


def _config_asset_urls(app_config: AppConfig) -> tuple[str | None, str | None, str | None]:
    branding = app_config.general_settings.ui_branding
    return branding.logo_mark_url, branding.logo_full_url, branding.favicon_url


def _safe_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    normalized = PurePosixPath(filename.replace("\\", "/")).name.strip()
    normalized = "".join(
        character for character in normalized if ord(character) >= 32 and ord(character) != 127
    )
    return normalized[:255] or None


def _validate_svg(content: bytes) -> None:
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("SVG files cannot contain document type or entity declarations")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError("SVG file is not valid XML") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("SVG file must have an <svg> root element")

    for element in root.iter():
        element_name = element.tag.rsplit("}", 1)[-1].lower()
        if element_name in _DISALLOWED_SVG_ELEMENTS:
            raise ValueError(f"SVG files cannot contain <{element_name}> elements")
        for raw_name, raw_value in element.attrib.items():
            attribute_name = raw_name.rsplit("}", 1)[-1].lower()
            value = str(raw_value).strip()
            lowered_value = value.lower()
            if attribute_name.startswith("on") or "javascript:" in lowered_value:
                raise ValueError("SVG files cannot contain executable attributes")
            if attribute_name == "href" and value and not value.startswith("#"):
                raise ValueError("SVG files cannot reference external resources")
            for match in _SVG_URL_PATTERN.finditer(value):
                target = match.group(1).strip().strip("\"'")
                if not target.startswith("#"):
                    raise ValueError("SVG files cannot reference external resources")


def validate_branding_asset(
    asset_key: UIBrandingAssetKind,
    content: bytes,
    *,
    original_filename: str | None,
) -> UIBrandingAsset:
    if not content:
        raise ValueError("uploaded branding asset is empty")
    if len(content) > BRANDING_ASSET_MAX_BYTES:
        raise ValueError("branding assets must be 2 MB or smaller")

    content_type: str | None = None
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        content_type = "image/webp"
    elif content.startswith(b"\x00\x00\x01\x00"):
        content_type = "image/x-icon"
    else:
        candidate = content.lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
        if candidate.startswith((b"<svg", b"<?xml", b"<!--")):
            _validate_svg(content)
            content_type = "image/svg+xml"

    if content_type is None:
        raise ValueError("branding assets must be PNG, JPEG, WebP, SVG, or ICO files")
    if content_type == "image/x-icon" and asset_key != "favicon":
        raise ValueError("ICO files can only be used as the favicon")

    digest = hashlib.sha256(content).hexdigest()
    return UIBrandingAsset(
        asset_key=asset_key,
        content_type=content_type,
        content=content,
        content_sha256=digest,
        size_bytes=len(content),
        original_filename=_safe_filename(original_filename),
    )


class UIBrandingAssetService:
    """Database-backed branding assets cached in each application replica."""

    def __init__(self, db_client: BrandingAssetDatabase) -> None:
        self.repository = UIBrandingAssetRepository(db_client)
        self._assets: dict[UIBrandingAssetKind, UIBrandingAsset] = {}
        self._tracked_urls: tuple[str | None, str | None, str | None] | None = None
        self._refresh_lock = asyncio.Lock()

    async def initialize(self, app_config: AppConfig) -> None:
        await self.refresh()
        self._tracked_urls = _config_asset_urls(app_config)

    async def on_config_change(self, app_config: AppConfig, _changes: dict[str, list[str]]) -> None:
        next_urls = _config_asset_urls(app_config)
        if next_urls == self._tracked_urls:
            return
        self._tracked_urls = next_urls
        await self.refresh()

    async def refresh(self) -> None:
        async with self._refresh_lock:
            rows = await self.repository.list_known()
            assets: dict[UIBrandingAssetKind, UIBrandingAsset] = {}
            for row in rows:
                raw_asset_key = str(row.asset_key or "")
                try:
                    asset_key = normalize_asset_kind(raw_asset_key)
                    encoded = row.content_base64
                    if isinstance(encoded, bytes):
                        encoded = encoded.decode("ascii")
                    # PostgreSQL's encode(..., 'base64') inserts RFC 2045 line
                    # breaks. Remove ASCII whitespace before strict decoding.
                    compact_encoded = "".join(str(encoded or "").split())
                    content = base64.b64decode(compact_encoded, validate=True)
                    asset = validate_branding_asset(
                        asset_key,
                        content,
                        original_filename=(
                            str(row.original_filename)
                            if row.original_filename is not None
                            else None
                        ),
                    )
                    if (
                        asset.content_type != str(row.content_type or "")
                        or asset.content_sha256 != str(row.content_sha256 or "")
                        or asset.size_bytes != int(row.size_bytes or 0)
                    ):
                        raise ValueError("branding asset metadata does not match its content")
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "skipping invalid cached branding asset key=%s: %s", raw_asset_key, exc
                    )
                    continue
                assets[asset_key] = asset
            self._assets = assets

    async def get_asset(
        self,
        asset_key: UIBrandingAssetKind,
        *,
        expected_sha256: str | None = None,
    ) -> UIBrandingAsset | None:
        asset = self._assets.get(asset_key)
        if expected_sha256 is not None and (
            asset is None or asset.content_sha256 != expected_sha256
        ):
            if self._tracked_urls is not None:
                tracked_url = self._tracked_urls[_ASSET_URL_INDEX[asset_key]]
                if tracked_url is None or f"v={expected_sha256}" not in tracked_url:
                    return None
            await self.refresh()
            asset = self._assets.get(asset_key)
        if expected_sha256 is not None and (
            asset is None or asset.content_sha256 != expected_sha256
        ):
            return None
        return asset
