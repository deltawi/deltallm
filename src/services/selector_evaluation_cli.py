"""Usage: uv run python -m src.services.selector_evaluation_cli fixtures.json"""

import argparse
from pathlib import Path
import sys

from pydantic import ValidationError

from src.services.selector_evaluation import (
    MAX_EVALUATION_BYTES,
    SelectorEvaluationRequest,
    evaluate_selector,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay labeled selector fixtures without provider calls"
    )
    parser.add_argument("fixtures", type=Path)
    args = parser.parse_args()
    try:
        with args.fixtures.open("rb") as fixture:
            payload = fixture.read(MAX_EVALUATION_BYTES + 1)
        if len(payload) > MAX_EVALUATION_BYTES:
            raise ValueError("Oversized evaluation")
        request = SelectorEvaluationRequest.model_validate_json(payload)
        print(evaluate_selector(request).model_dump_json(indent=2))
        return 0
    except (OSError, ValueError, ValidationError):
        # Validation errors can contain fixture content. Do not echo them.
        print(
            "Invalid or unreadable selector fixtures (maximum 100 samples / 256 KiB).",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
