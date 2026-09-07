from __future__ import annotations

import json

from src.route_policy_contract import LLMTierSelectorPolicy, SelectorLane
from src.router.selection.contracts import SELECTOR_OUTPUT_BYTES, SelectorCause


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate selector result field")
        result[key] = value
    return result


def parse_selector_output(text: str, policy: LLMTierSelectorPolicy) -> SelectorLane | SelectorCause:
    # Check characters before encoding so even an invalid injected hop stays bounded.
    if len(text) > SELECTOR_OUTPUT_BYTES:
        return SelectorCause.OUTPUT_TOO_LARGE
    try:
        if len(text.encode("utf-8")) > SELECTOR_OUTPUT_BYTES:
            return SelectorCause.OUTPUT_TOO_LARGE
        document = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        return SelectorCause.INVALID_JSON
    if not isinstance(document, dict) or set(document) != {"lane"}:
        return SelectorCause.INVALID_JSON
    lane_id = document["lane"]
    if not isinstance(lane_id, str):
        return SelectorCause.INVALID_JSON
    for lane in policy.lanes:
        if lane.id == lane_id:
            return lane
    return SelectorCause.UNKNOWN_LANE
