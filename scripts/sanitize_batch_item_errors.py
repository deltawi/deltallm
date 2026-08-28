#!/usr/bin/env python3
"""Inspect or sanitize historical terminal batch-item errors in bounded pages."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.batch.error_remediation import remediate_terminal_batch_item_errors
from src.batch.repositories.error_remediation_repository import BatchErrorRemediationRepository

_MAX_DATABASE_URL_FILE_CHARS = 8_192


def _bounded_positive_int(value: str, *, argument: str, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{argument} must be an integer") from exc
    if not 1 <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"{argument} must be between 1 and {maximum}")
    return parsed


def _page_size(value: str) -> int:
    return _bounded_positive_int(value, argument="--page-size", maximum=1_000)


def _max_pages(value: str) -> int:
    return _bounded_positive_int(value, argument="--max-pages", maximum=10_000)


def _read_database_url(path_value: str) -> str:
    with Path(path_value).open(encoding="utf-8") as database_url_file:
        database_url = database_url_file.read(_MAX_DATABASE_URL_FILE_CHARS + 1)
    if len(database_url) > _MAX_DATABASE_URL_FILE_CHARS:
        raise ValueError("database URL file is too large")
    database_url = database_url.strip()
    if not database_url:
        raise ValueError("database URL file must not be empty")
    return database_url


async def _run(args: argparse.Namespace) -> int:
    from prisma import Prisma

    database_url = _read_database_url(args.database_url_file)
    db = Prisma(datasource={"url": database_url})
    await db.connect()
    try:
        result = await remediate_terminal_batch_item_errors(
            BatchErrorRemediationRepository(db),
            after_item_id=args.after_item_id,
            page_size=args.page_size,
            max_pages=args.max_pages,
            apply=args.command == "apply",
        )
    finally:
        await db.disconnect()
    print(
        f"inspected={result.inspected} updated={result.updated} "
        f"has_more={str(result.has_more).lower()} "
        f"next_after_item_id={result.next_after_item_id or ''}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "apply"))
    parser.add_argument("--database-url-file", required=True)
    parser.add_argument("--after-item-id")
    parser.add_argument("--page-size", type=_page_size, default=500)
    parser.add_argument("--max-pages", type=_max_pages, default=100)
    parser.add_argument("--confirm-sanitize", action="store_true")
    args = parser.parse_args()
    if args.command == "apply" and not args.confirm_sanitize:
        parser.error("apply requires --confirm-sanitize")
    try:
        exit_code = asyncio.run(_run(args))
    except Exception as exc:
        print(f"batch error sanitization failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
