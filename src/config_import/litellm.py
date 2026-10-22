"""Convert a LiteLLM proxy `config.yaml` into a DeltaLLM `config.yaml`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import ValidationError

from src.config import AppConfig
from src.config_import.litellm_deployments import convert_model_list
from src.config_import.litellm_settings import (
    SettingsSections,
    convert_general_settings,
    convert_guardrails,
    convert_litellm_settings,
    convert_router_settings,
)
from src.config_import.report import ImportReport, Severity, is_env_reference

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_DEPLOYMENTS = 5_000

_KNOWN_SECTIONS = frozenset(
    {"model_list", "router_settings", "litellm_settings", "general_settings", "guardrails"}
)

OUTPUT_HEADER = """\
# DeltaLLM configuration converted from a LiteLLM config.yaml.
# Read the import report before the first start. Secrets come from environment variables.
# Guide: docs/guides/migrate-from-litellm.md
"""


class ConfigImportError(ValueError):
    """The input cannot be converted. The message contains no configuration values."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    config: dict[str, Any]
    report: ImportReport

    def to_yaml(self) -> str:
        body = yaml.safe_dump(self.config, sort_keys=False, allow_unicode=True, width=100)
        return OUTPUT_HEADER + body


def parse_litellm_yaml(text: str) -> Mapping[str, Any]:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ConfigImportError(
            f"the input file is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB"
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise ConfigImportError(f"the input is not valid YAML{where}") from None
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ConfigImportError("the input must be a YAML mapping with a model_list section")
    return data


def convert_litellm_config(raw: Mapping[str, Any]) -> ImportResult:
    """Convert a parsed LiteLLM config. Raise ConfigImportError when the result is not valid."""

    report = ImportReport()
    model_list = raw.get("model_list")
    if isinstance(model_list, list) and len(model_list) > MAX_DEPLOYMENTS:
        raise ConfigImportError(f"model_list has more than {MAX_DEPLOYMENTS} entries")
    deployments = convert_model_list(model_list, report)
    sections = SettingsSections()
    convert_router_settings(raw.get("router_settings"), sections, report)
    convert_litellm_settings(raw.get("litellm_settings"), sections, report)
    convert_guardrails(raw.get("guardrails"), sections, report)
    convert_general_settings(raw.get("general_settings"), sections, report)
    _report_other_sections(raw, report)
    _check_fallback_targets(deployments, sections, report)

    config: dict[str, Any] = {"model_list": deployments}
    if sections.router:
        config["router_settings"] = sections.router
    if sections.deltallm:
        config["deltallm_settings"] = sections.deltallm
    config["general_settings"] = sections.general
    _validate(config)
    return ImportResult(config=config, report=report)


def _report_other_sections(raw: Mapping[str, Any], report: ImportReport) -> None:
    env = raw.get("environment_variables")
    if isinstance(env, Mapping) and env:
        names = ", ".join(sorted(str(name) for name in env))
        report.add(
            Severity.ACTION,
            "environment_variables",
            f"set these in the DeltaLLM environment (values not copied): {names}.",
        )
    for key in raw:
        if key in _KNOWN_SECTIONS or key == "environment_variables":
            continue
        report.add(Severity.SKIPPED, str(key), "this section has no equivalent.")


def _check_fallback_targets(
    deployments: list[dict[str, Any]], sections: SettingsSections, report: ImportReport
) -> None:
    names = {str(item["model_name"]) for item in deployments}
    names.update(sections.router.get("model_group_alias", {}))
    for key in ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks"):
        for chain in sections.deltallm.get(key, []):
            for model, fallbacks in chain.items():
                for name in [model, *fallbacks]:
                    if name not in names:
                        report.add(
                            Severity.ACTION,
                            f"deltallm_settings.{key}.{model}",
                            f"refers to `{name}`, which is not an imported model.",
                        )


# Env references are resolved only at startup. Validation sees a stand-in value that passes
# the length checks on secrets, so it checks the structure and not the secret values.
_ENV_STAND_IN = "env-reference-resolved-at-startup-0000000000"


def _with_env_stand_ins(value: Any) -> Any:
    if is_env_reference(value):
        return _ENV_STAND_IN
    if isinstance(value, dict):
        return {key: _with_env_stand_ins(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_with_env_stand_ins(item) for item in value]
    return value


def _validate(config: dict[str, Any]) -> None:
    try:
        AppConfig.model_validate(_with_env_stand_ins(config))
    except ValidationError as exc:
        details = []
        for error in exc.errors(include_url=False, include_context=False, include_input=False)[:5]:
            location = ".".join(str(part) for part in error.get("loc") or ()) or "<config>"
            details.append(f"{location}: {error.get('msg')}")
        raise ConfigImportError("the converted configuration is not valid: " + "; ".join(details))
