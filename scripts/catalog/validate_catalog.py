from __future__ import annotations

from src.providers.model_catalog_loader import provider_catalog_summary, reload_provider_catalogs


def main() -> int:
    reload_provider_catalogs()
    for summary in provider_catalog_summary():
        print(
            f"{summary['provider']}: {summary['model_count']} models "
            f"(last_verified_at={summary['last_verified_at']}, source_type={summary['source_type']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
