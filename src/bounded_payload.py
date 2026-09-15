"""Reject retained work before copying or serializing an unbounded object graph."""

from __future__ import annotations

from datetime import date, datetime
from sys import getsizeof

from pydantic import BaseModel


class PayloadCapacityExceeded(ValueError):
    pass


def retained_size(value: object, *, limit: int, max_nodes: int = 100_000) -> int:
    """Conservative recursive Python-object cost; shared references count each time.

    Counting repeated references bounds subsequent serialization/copy work as well
    as memory. Depth and node limits reject cycles and pathological tiny-object trees.
    Arbitrary objects are rejected instead of invoking user-defined serialization.
    """
    size = 0
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal size, nodes
        nodes += 1
        size += getsizeof(item)
        if depth > 32 or nodes > max_nodes or size > limit:
            raise PayloadCapacityExceeded("Work payload exceeds its allocation")
        if isinstance(item, BaseModel):
            visit(item.__dict__, depth + 1)
            visit(item.__pydantic_extra__, depth + 1)
            visit(item.__pydantic_private__, depth + 1)
            visit(tuple(item.__pydantic_fields_set__), depth + 1)
        elif isinstance(item, dict):
            for key, val in item.items():
                if not isinstance(key, str):
                    raise PayloadCapacityExceeded("Unsupported payload key")
                visit(key, depth + 1)
                visit(val, depth + 1)
        elif isinstance(item, (list, tuple)):
            for val in item:
                visit(val, depth + 1)
        elif item is not None and not isinstance(
            item, (str, bytes, int, float, bool, datetime, date)
        ):
            raise PayloadCapacityExceeded("Unsupported payload value")

    visit(value, 0)
    return size
