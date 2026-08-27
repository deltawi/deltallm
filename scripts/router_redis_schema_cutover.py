#!/usr/bin/env python3
"""Inspect or clear bounded legacy router state during the Redis v1 cutover."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from redis.asyncio import Redis


LEGACY_ROUTER_KEY_PATTERNS = (
    "active_requests:*",
    "cooldown:*",
    "failures:*",
    "health:*",
    "latency:*",
    "usage_*:*",
)


@dataclass(frozen=True, slots=True)
class LegacyRouterState:
    keys: tuple[str, ...]
    active_request_count: int


async def scan_legacy_router_state(
    redis_client: Any,
    *,
    scan_count: int,
    max_scan_pages_per_pattern: int,
    max_keys: int,
) -> LegacyRouterState:
    """Return a bounded snapshot of legacy keys without mutating Redis."""
    discovered: set[str] = set()
    for pattern in LEGACY_ROUTER_KEY_PATTERNS:
        cursor = 0
        for _page in range(max_scan_pages_per_pattern):
            cursor, raw_keys = await redis_client.scan(
                cursor=cursor,
                match=pattern,
                count=scan_count,
            )
            discovered.update(
                key.decode("utf-8") if isinstance(key, bytes) else str(key) for key in raw_keys
            )
            if len(discovered) > max_keys:
                raise RuntimeError(
                    f"legacy router key count exceeds the configured limit of {max_keys}"
                )
            if int(cursor) == 0:
                break
        else:
            raise RuntimeError(
                f"legacy router scan exceeded {max_scan_pages_per_pattern} pages for {pattern!r}"
            )

    active_keys = sorted(key for key in discovered if key.startswith("active_requests:"))
    active_request_count = 0
    for offset in range(0, len(active_keys), scan_count):
        values = await redis_client.mget(active_keys[offset : offset + scan_count])
        try:
            parsed_values = [int(value or 0) for value in values]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("legacy active-request state contains a non-integer value") from exc
        if any(value < 0 for value in parsed_values):
            raise RuntimeError("legacy active-request state contains a negative value")
        active_request_count += sum(parsed_values)
    return LegacyRouterState(
        keys=tuple(sorted(discovered)),
        active_request_count=active_request_count,
    )


async def delete_legacy_router_keys(
    redis_client: Any,
    keys: Sequence[str],
    *,
    delete_batch_size: int,
) -> int:
    """Delete an already-bounded legacy-key snapshot in bounded batches."""
    deleted = 0
    for offset in range(0, len(keys), delete_batch_size):
        deleted += int(await redis_client.delete(*keys[offset : offset + delete_batch_size]))
    return deleted


def resolve_redis_url(*, redis_url: str | None, redis_url_file: str | None) -> str:
    """Resolve a non-empty Redis URL without logging its value."""
    resolved = redis_url
    if redis_url_file:
        resolved = Path(redis_url_file).read_text(encoding="utf-8").strip()
    if not resolved:
        raise ValueError("Redis URL must not be empty")
    return resolved


async def _run(args: argparse.Namespace) -> int:
    redis_client = Redis.from_url(
        args.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    try:
        state = await scan_legacy_router_state(
            redis_client,
            scan_count=args.scan_count,
            max_scan_pages_per_pattern=args.max_scan_pages_per_pattern,
            max_keys=args.max_keys,
        )
        print(f"legacy_router_keys={len(state.keys)} active_requests={state.active_request_count}")
        if state.active_request_count > 0:
            print("refusing to continue while legacy requests are active")
            return 2
        if args.action == "clear":
            if not args.confirm_clear_legacy_router_state:
                print("clear requires --confirm-clear-legacy-router-state")
                return 2
            deleted = await delete_legacy_router_keys(
                redis_client,
                state.keys,
                delete_batch_size=args.delete_batch_size,
            )
            print(f"deleted_legacy_router_keys={deleted}")
        return 0
    finally:
        await redis_client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "clear"))
    redis_url_group = parser.add_mutually_exclusive_group(required=True)
    redis_url_group.add_argument("--redis-url")
    redis_url_group.add_argument(
        "--redis-url-file",
        help="read the Redis URL from a file so credentials do not appear in process arguments",
    )
    parser.add_argument("--scan-count", type=int, default=100, choices=range(1, 1001))
    parser.add_argument("--max-scan-pages-per-pattern", type=int, default=1000)
    parser.add_argument("--max-keys", type=int, default=10_000)
    parser.add_argument("--delete-batch-size", type=int, default=100, choices=range(1, 1001))
    parser.add_argument("--confirm-clear-legacy-router-state", action="store_true")
    args = parser.parse_args()
    if args.max_scan_pages_per_pattern < 1 or args.max_keys < 1:
        parser.error("scan page and key limits must be positive")
    try:
        args.redis_url = resolve_redis_url(
            redis_url=args.redis_url,
            redis_url_file=args.redis_url_file,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
