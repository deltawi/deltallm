import pytest

from src.router.selection.contracts import SelectorCause
from src.router.selection.parser import parse_selector_output


@pytest.mark.parametrize("lane", ["economy", "quality"])
def test_exact_lane_has_policy_owned_rank(selector_policy, lane):
    result = parse_selector_output(' \n{"lane":"' + lane + '"}\t', selector_policy)
    assert result == next(item for item in selector_policy.lanes if item.id == lane)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "null",
        '"economy"',
        '{"lane":null}',
        '{"lane":0}',
        '{"lane":"economy","rank":0}',
        '{"lane":"economy","lane":"quality"}',
        '{"lane":"economy"}{"lane":"quality"}',
        '```json\n{"lane":"economy"}\n```',
        '{"deployment_id":"classifier-concrete"}',
        '{"lane":"economy",}',
        "\ud800",
        "[" * 256,
    ],
)
def test_invalid_json_is_not_repaired(selector_policy, text):
    assert parse_selector_output(text, selector_policy) is SelectorCause.INVALID_JSON


@pytest.mark.parametrize("lane", ["Economy", "economy ", " quality", "dep-mini", "other"])
def test_unknown_lane_is_not_normalized(selector_policy, lane):
    assert (
        parse_selector_output('{"lane":"' + lane + '"}', selector_policy)
        is SelectorCause.UNKNOWN_LANE
    )


def test_output_byte_boundary(selector_policy):
    valid = '{"lane":"economy"}'
    assert parse_selector_output(valid.ljust(256), selector_policy).id == "economy"
    assert (
        parse_selector_output(valid.ljust(257), selector_policy) is SelectorCause.OUTPUT_TOO_LARGE
    )
    assert parse_selector_output("😀" * 65, selector_policy) is SelectorCause.OUTPUT_TOO_LARGE
