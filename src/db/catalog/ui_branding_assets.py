from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal, Protocol

UIBrandingAssetKind = Literal["logo_mark", "logo_full", "favicon"]
UI_BRANDING_ASSET_KEYS: tuple[UIBrandingAssetKind, ...] = (
    "logo_mark",
    "logo_full",
    "favicon",
)


class BrandingAssetDatabase(Protocol):
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]: ...

    async def execute_raw(self, query: str, *params: object) -> object: ...


@dataclass(frozen=True, slots=True)
class UIBrandingAssetRow:
    asset_key: object
    content_type: object
    content_base64: object
    content_sha256: object
    size_bytes: object
    original_filename: object


class UIBrandingAssetRepository:
    """PostgreSQL persistence for the installation-wide branding assets."""

    def __init__(self, db: BrandingAssetDatabase) -> None:
        self.db = db

    async def list_known(self) -> list[UIBrandingAssetRow]:
        rows = await self.db.query_raw(
            """
            SELECT asset_key,
                   content_type,
                   encode(content, 'base64') AS content_base64,
                   content_sha256,
                   size_bytes,
                   original_filename
            FROM deltallm_ui_branding_asset
            WHERE asset_key IN ($1, $2, $3)
            """,
            *UI_BRANDING_ASSET_KEYS,
        )
        return [
            UIBrandingAssetRow(
                asset_key=row.get("asset_key"),
                content_type=row.get("content_type"),
                content_base64=row.get("content_base64"),
                content_sha256=row.get("content_sha256"),
                size_bytes=row.get("size_bytes"),
                original_filename=row.get("original_filename"),
            )
            for row in rows
        ]

    async def upsert(
        self,
        *,
        asset_key: UIBrandingAssetKind,
        content_type: str,
        content: bytes,
        content_sha256: str,
        size_bytes: int,
        original_filename: str | None,
        updated_by: str,
    ) -> None:
        encoded = base64.b64encode(content).decode("ascii")
        await self.db.execute_raw(
            """
            INSERT INTO deltallm_ui_branding_asset (
                asset_key, content_type, content, content_sha256, size_bytes,
                original_filename, updated_by, created_at, updated_at
            )
            VALUES ($1, $2, decode($3, 'base64'), $4, $5, $6, $7, NOW(), NOW())
            ON CONFLICT (asset_key) DO UPDATE
            SET content_type = EXCLUDED.content_type,
                content = EXCLUDED.content,
                content_sha256 = EXCLUDED.content_sha256,
                size_bytes = EXCLUDED.size_bytes,
                original_filename = EXCLUDED.original_filename,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            """,
            asset_key,
            content_type,
            encoded,
            content_sha256,
            size_bytes,
            original_filename,
            updated_by,
        )

    async def delete(self, asset_key: UIBrandingAssetKind) -> None:
        await self.db.execute_raw(
            "DELETE FROM deltallm_ui_branding_asset WHERE asset_key = $1",
            asset_key,
        )

    async def delete_all_known(self) -> None:
        await self.db.execute_raw(
            "DELETE FROM deltallm_ui_branding_asset WHERE asset_key IN ($1, $2, $3)",
            *UI_BRANDING_ASSET_KEYS,
        )
