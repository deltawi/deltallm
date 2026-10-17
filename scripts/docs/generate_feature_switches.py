"""Generate the feature table in docs/deployment/feature-switches.md.

The table comes from scripts/docs/feature_switches.yaml. Before writing, the
generator loads every profile file and fails when a profile or the built-in
default disagrees with that list.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.docs.generated_artifact import write_or_check
from src.config import AppConfig
from src.config_runtime.dynamic import _STARTUP_ONLY_GENERAL_SETTINGS, _STARTUP_TIER_SETTINGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT_ROOT / "scripts" / "docs" / "feature_switches.yaml"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "deployment" / "feature-switches.md"
HELM_CHART = PROJECT_ROOT / "deploy" / "kubernetes" / "helm"
START_MARKER = (
    "<!-- feature-switches:start (generated; edit scripts/docs/feature_switches.yaml) -->"
)
END_MARKER = "<!-- feature-switches:end -->"
STARTUP_ONLY = _STARTUP_ONLY_GENERAL_SETTINGS | frozenset(_STARTUP_TIER_SETTINGS)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the artifact is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _without_env_references(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_env_references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_env_references(item) for item in value]
    if isinstance(value, str) and value.startswith("os.environ/"):
        return None
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _profile_config(profile: dict[str, Any]) -> dict[str, Any]:
    if "file" in profile:
        raw = _load_yaml(PROJECT_ROOT / profile["file"])
    else:
        # Same layering as the chart: its defaults file, values.yaml, then the overlay.
        raw = {
            "general_settings": _load_yaml(HELM_CHART / "files/config-defaults.yaml")[
                "general_settings"
            ]
        }
        for path in (HELM_CHART / "values.yaml", PROJECT_ROOT / profile["helm_overlay"]):
            raw = _deep_merge(raw, _load_yaml(path).get("config") or {})
    config = AppConfig.model_validate(_without_env_references(raw))
    return json.loads(config.model_dump_json())


def _lookup(config: dict[str, Any], dotted: str) -> Any:
    value: Any = config
    for part in dotted.split("."):
        value = value[part]
    return value


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return f"`{value}`"


def generate(source: Path = SOURCE) -> tuple[str, list[str]]:
    data = _load_yaml(source)
    profiles: dict[str, dict[str, Any]] = data["profiles"]
    defaults = json.loads(AppConfig().model_dump_json())
    effective = {name: _profile_config(profile) for name, profile in profiles.items()}
    errors: list[str] = []

    header = ["Feature", "Setting", "Default"]
    header += [profile["title"] for profile in profiles.values()]
    header += ["Restart", "Billing", "Needs"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for feature in data["features"]:
        setting = feature["setting"]
        default = _lookup(defaults, setting)
        cells = []
        for name in profiles:
            expected = feature.get("profiles", {}).get(name, default)
            actual = _lookup(effective[name], setting)
            if actual != expected:
                errors.append(
                    f"{setting}: {profiles[name]['title']} profile is {actual!r}, "
                    f"{source.name} says {expected!r}"
                )
            cells.append(_render_value(expected) if expected != default else "default")
        section, _, field = setting.partition(".")
        top_field = field.split(".", 1)[0]
        startup_only = section == "general_settings" and top_field in STARTUP_ONLY
        restart = "Yes" if startup_only or feature.get("restart") else "No"
        title = feature["feature"]
        if feature.get("link"):
            title = f"[{title}]({feature['link']})"
        short_setting = setting.removeprefix("general_settings.")
        row = [title, f"`{short_setting}`", _render_value(default), *cells]
        row += [restart, str(feature["billing"]), str(feature["needs"])]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines), errors


def render_page(table: str, page: str) -> str:
    start = page.index(START_MARKER) + len(START_MARKER)
    end = page.index(END_MARKER)
    return page[:start] + "\n\n" + table + "\n\n" + page[end:]


def main() -> int:
    args = _parse_args()
    table, errors = generate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    page = args.output.read_text(encoding="utf-8")
    ok = write_or_check(args.output, render_page(table, page), check=args.check)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
