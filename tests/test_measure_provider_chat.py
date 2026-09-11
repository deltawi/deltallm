from argparse import Namespace

import pytest

from scripts.measure_provider_chat import measure


@pytest.mark.parametrize(
    "provider,stream,mcp",
    [
        ("openai", False, False),
        ("openai", True, False),
        ("deepseek", False, False),
        ("minimax", True, False),
        ("deepseek", False, True),
        ("minimax", False, True),
    ],
)
async def test_provider_measurement_retains_wire_call_and_cleanup_budgets(
    tmp_path, provider, stream, mcp
):
    await measure(
        Namespace(
            provider=provider,
            stream=stream,
            mcp=mcp,
            rate=10,
            duration=0.1,
            output=tmp_path,
        )
    )
