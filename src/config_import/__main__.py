"""Command line entry point: `python -m src.config_import litellm <input> -o <output>`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from src.config_import.litellm import (
    MAX_INPUT_BYTES,
    ConfigImportError,
    convert_litellm_config,
    parse_litellm_yaml,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_STRICT_SKIPPED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.config_import",
        description="Convert another gateway's configuration into a DeltaLLM config.yaml.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    litellm = commands.add_parser("litellm", help="Convert a LiteLLM proxy config.yaml.")
    litellm.add_argument("input", type=Path, help="LiteLLM config.yaml to read")
    litellm.add_argument("-o", "--output", type=Path, required=True, help="file to write")
    litellm.add_argument("--report", type=Path, help="also write the report as Markdown")
    litellm.add_argument(
        "--force", action="store_true", help="replace the output and report files if they exist"
    )
    litellm.add_argument(
        "--strict",
        action="store_true",
        help=f"exit with {EXIT_STRICT_SKIPPED} when a deployment or setting was not imported",
    )
    return parser


def run_litellm(args: argparse.Namespace) -> int:
    targets = [path for path in (args.output, args.report) if path is not None]
    for path in targets:
        if path.resolve() == args.input.resolve():
            return _fail(f"{path} is the input file. Choose another output path.")
        if path.exists() and not args.force:
            return _fail(f"{path} exists. Use --force to replace it.")
    try:
        if args.input.stat().st_size > MAX_INPUT_BYTES:
            raise ConfigImportError("the input file is larger than 5 MB")
        text = args.input.read_text(encoding="utf-8")
        result = convert_litellm_config(parse_litellm_yaml(text))
    except OSError as exc:
        return _fail(f"cannot read {args.input}: {exc.strerror or exc}")
    except UnicodeDecodeError:
        return _fail(f"{args.input} is not UTF-8 text.")
    except ConfigImportError as exc:
        return _fail(str(exc))

    _write_private(args.output, result.to_yaml())
    if args.report is not None:
        _write_private(args.report, result.report.render_markdown(source_name=args.input.name))
    sys.stdout.write(result.report.render_text())
    sys.stdout.write(f"\nWrote {args.output}.\n")
    if args.strict and result.report.has_skipped:
        return EXIT_STRICT_SKIPPED
    return EXIT_OK


def _write_private(path: Path, content: str) -> None:
    """Write with owner-only permissions: the output can hold literal secrets from the input."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _fail(message: str) -> int:
    sys.stderr.write(f"error: {message}\n")
    return EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "litellm":
        return run_litellm(args)
    return EXIT_FAILED  # pragma: no cover - argparse enforces a known command


if __name__ == "__main__":
    raise SystemExit(main())
