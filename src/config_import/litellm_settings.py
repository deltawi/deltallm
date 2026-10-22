"""Convert LiteLLM `router_settings`, `litellm_settings`, `general_settings` and `guardrails`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.config_import.report import ImportReport, Severity, flag_literal_secrets

_STRATEGIES: dict[str, str] = {
    "simple-shuffle": "simple-shuffle",
    "least-busy": "least-busy",
    "latency-based-routing": "latency-based-routing",
    "cost-based-routing": "cost-based-routing",
    "usage-based-routing": "usage-based-routing",
    "usage-based-routing-v2": "usage-based-routing",
}
_ROUTER_SAME_KEYS = frozenset(
    {"num_retries", "retry_after", "timeout", "cooldown_time", "allowed_fails"}
)
_ROUTER_REDIS_KEYS = {
    "redis_host": "redis_host",
    "redis_port": "redis_port",
    "redis_password": "redis_password",
    "redis_url": "redis_url",
}
FALLBACK_KEYS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")
_SUPPORTED_CALLBACKS = frozenset({"langfuse", "prometheus", "otel", "opentelemetry", "s3"})
_CALLBACK_KEYS = ("success_callback", "failure_callback", "callbacks")
# DeltaLLM's s3 response cache backend is not implemented, so LiteLLM s3 caches are skipped.
_CACHE_BACKENDS = {"local": "memory", "redis": "redis"}
_GENERAL_RENAMES = {
    "database_connection_pool_limit": "db_pool_size",
    "database_connection_timeout": "db_pool_timeout",
    "background_health_checks": "background_health_checks",
    "health_check_interval": "health_check_interval",
    "enable_jwt_auth": "enable_jwt_auth",
    "litellm_key_header_name": "deltallm_key_header_name",
    "user_api_key_cache_ttl": "api_key_auth_cache_ttl_seconds",
}
_GENERAL_INTEGER_KEYS = frozenset(
    {"db_pool_size", "db_pool_timeout", "health_check_interval", "api_key_auth_cache_ttl_seconds"}
)
_GUARDRAIL_CLASSES = {
    "presidio": "src.guardrails.presidio.PresidioGuardrail",
    "lakera": "src.guardrails.lakera.LakeraGuardrail",
    "lakera_v2": "src.guardrails.lakera.LakeraGuardrail",
    "lakera_prompt_injection": "src.guardrails.lakera.LakeraGuardrail",
}
_GUARDRAIL_MODES = {"pre_call": "pre_call", "during_call": "pre_call", "post_call": "post_call"}


@dataclass(slots=True)
class SettingsSections:
    router: dict[str, Any] = field(default_factory=dict)
    deltallm: dict[str, Any] = field(default_factory=dict)
    general: dict[str, Any] = field(default_factory=dict)


def convert_router_settings(raw: object, out: SettingsSections, report: ImportReport) -> None:
    for key, value in _mapping(raw, "router_settings", report).items():
        path = f"router_settings.{key}"
        if value is None:
            continue
        if key == "routing_strategy":
            strategy = _STRATEGIES.get(str(value))
            if strategy is None:
                report.add(Severity.SKIPPED, path, "is not supported; simple-shuffle is used.")
            else:
                out.router[key] = strategy
                if strategy != value:
                    report.add(Severity.CHANGED, path, f"became `{strategy}`.")
        elif key in _ROUTER_SAME_KEYS or key == "enable_pre_call_checks":
            out.router[key] = value
        elif key == "model_group_alias":
            _convert_aliases(value, path, out, report)
        elif key in FALLBACK_KEYS:
            merge_fallbacks(out.deltallm, key, value, path, report)
        elif key in _ROUTER_REDIS_KEYS:
            out.general[_ROUTER_REDIS_KEYS[key]] = value
            flag_literal_secrets(report, "router_settings", [(key, value)])
        elif key == "enable_tag_filtering":
            report.add(Severity.INFO, path, "use routing_strategy tag-based-routing in DeltaLLM.")
        else:
            report.add(Severity.SKIPPED, path, "has no equivalent.")


def convert_litellm_settings(raw: object, out: SettingsSections, report: ImportReport) -> None:
    settings = _mapping(raw, "litellm_settings", report)
    for key, value in settings.items():
        path = f"litellm_settings.{key}"
        if value is None or key in {"cache", "cache_params"}:
            continue
        if key in FALLBACK_KEYS:
            merge_fallbacks(out.deltallm, key, value, path, report)
        elif key in _CALLBACK_KEYS:
            _convert_callbacks(key, value, path, out, report)
        elif key == "turn_off_message_logging":
            out.deltallm[key] = bool(value)
        elif key == "num_retries":
            out.router.setdefault("num_retries", value)
        elif key == "request_timeout":
            out.router.setdefault("timeout", value)
        else:
            report.add(Severity.SKIPPED, path, "has no equivalent.")
    _convert_cache(settings, out, report)


def convert_general_settings(raw: object, out: SettingsSections, report: ImportReport) -> None:
    general = _mapping(raw, "general_settings", report)
    for key, value in general.items():
        path = f"general_settings.{key}"
        if value is None or key in {"master_key", "database_url"}:
            continue
        if key in _GENERAL_RENAMES:
            target = _GENERAL_RENAMES[key]
            if target in _GENERAL_INTEGER_KEYS:
                value = _whole_number(value)
                if value is None:
                    report.add(Severity.SKIPPED, path, "is not a whole number.")
                    continue
            out.general[target] = value
        elif key == "alerting":
            _convert_alerting(value, path, out, report)
        elif key == "store_model_in_db":
            report.add(Severity.INFO, path, "DeltaLLM stores deployments in the database.")
        else:
            report.add(Severity.SKIPPED, path, "has no equivalent.")
    _apply_required_general(general, out, report)


def convert_guardrails(raw: object, out: SettingsSections, report: ImportReport) -> None:
    if raw is None:
        return
    if not isinstance(raw, list):
        report.add(Severity.SKIPPED, "guardrails", "is not a list.")
        return
    converted: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        guardrail = _convert_guardrail(entry, f"guardrails[{index}]", report)
        if guardrail is not None:
            converted.append(guardrail)
    if converted:
        out.deltallm["guardrails"] = converted


def merge_fallbacks(
    target: dict[str, Any], key: str, value: object, path: str, report: ImportReport
) -> None:
    """Merge LiteLLM fallback chains ({model: [fallback, ...]}) keeping first-seen order."""

    if not isinstance(value, list):
        report.add(Severity.SKIPPED, path, "is not a list of {model: [fallbacks]}.")
        return
    chains: list[dict[str, list[str]]] = target.setdefault(key, [])
    for item in value:
        if not isinstance(item, Mapping):
            report.add(Severity.SKIPPED, path, "has an entry that is not {model: [fallbacks]}.")
            continue
        for model, fallbacks in item.items():
            if not isinstance(fallbacks, list):
                report.add(Severity.SKIPPED, f"{path}.{model}", "is not a list.")
                continue
            existing = next((chain for chain in chains if str(model) in chain), None)
            if existing is None:
                chains.append({str(model): [str(name) for name in fallbacks]})
                continue
            names = existing[str(model)]
            names.extend(str(name) for name in fallbacks if str(name) not in names)


def _whole_number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _mapping(raw: object, path: str, report: ImportReport) -> Mapping[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        report.add(Severity.SKIPPED, path, "is not a mapping.")
        return {}
    return raw


def _convert_aliases(value: object, path: str, out: SettingsSections, report: ImportReport) -> None:
    if not isinstance(value, Mapping):
        report.add(Severity.SKIPPED, path, "is not a mapping.")
        return
    aliases: dict[str, str] = {}
    for alias, target in value.items():
        if isinstance(target, Mapping):
            target = target.get("model")
            report.add(Severity.CHANGED, f"{path}.{alias}", "`hidden` is not supported.")
        if isinstance(target, str) and target:
            aliases[str(alias)] = target
    if aliases:
        out.router["model_group_alias"] = aliases


def _convert_callbacks(
    key: str, value: object, path: str, out: SettingsSections, report: ImportReport
) -> None:
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list):
        report.add(Severity.SKIPPED, path, "is not a list.")
        return
    kept = [str(name) for name in names if str(name) in _SUPPORTED_CALLBACKS]
    for name in names:
        if str(name) not in _SUPPORTED_CALLBACKS:
            report.add(Severity.SKIPPED, f"{path}.{name}", "is not a DeltaLLM integration.")
    if kept:
        out.deltallm[key] = kept
        report.add(
            Severity.ACTION,
            path,
            "check the integration settings in docs/features/observability.md. "
            "DeltaLLM uses deltallm_settings.callback_settings.",
        )


def _convert_cache(
    settings: Mapping[str, Any], out: SettingsSections, report: ImportReport
) -> None:
    if not settings.get("cache"):
        return
    params = settings.get("cache_params")
    params = params if isinstance(params, Mapping) else {}
    cache_type = str(params.get("type") or "local")
    backend = _CACHE_BACKENDS.get(cache_type)
    if backend is None:
        report.add(
            Severity.SKIPPED,
            "litellm_settings.cache_params.type",
            f"`{cache_type}` cache is not supported. The response cache stays off.",
        )
        return
    out.general["cache_enabled"] = True
    out.general["cache_backend"] = backend
    if isinstance(params.get("ttl"), int | float):
        out.general["cache_ttl"] = int(params["ttl"])
    for key in ("host", "port", "password"):
        if params.get(key) is not None and backend == "redis":
            out.general.setdefault(f"redis_{key}", params[key])
    flag_literal_secrets(report, "litellm_settings.cache_params", params.items())
    report.add(
        Severity.INFO,
        "litellm_settings.cache",
        "the response cache is on. Cache hits are charged like misses.",
    )


def _convert_alerting(
    value: object, path: str, out: SettingsSections, report: ImportReport
) -> None:
    names = value if isinstance(value, list) else [value]
    if "slack" in names:
        out.general["slack_alerting_enabled"] = True
        out.general["slack_webhook_url"] = "os.environ/SLACK_WEBHOOK_URL"
        report.add(Severity.ACTION, path, "set SLACK_WEBHOOK_URL for Slack alerts.")
    for name in names:
        if name != "slack":
            report.add(Severity.SKIPPED, f"{path}.{name}", "is not a DeltaLLM alert channel.")


def _apply_required_general(
    general: Mapping[str, Any], out: SettingsSections, report: ImportReport
) -> None:
    out.general["master_key"] = "os.environ/DELTALLM_MASTER_KEY"
    out.general["salt_key"] = "os.environ/DELTALLM_SALT_KEY"
    out.general["database_url"] = "os.environ/DATABASE_URL"
    if not any(key.startswith("redis_") for key in out.general):
        out.general["redis_url"] = "os.environ/REDIS_URL"
    out.general["model_deployment_source"] = "db_only"
    out.general["model_deployment_bootstrap_from_config"] = True
    report.add(
        Severity.ACTION,
        "general_settings.master_key",
        "set DELTALLM_MASTER_KEY (at least 32 characters, letters and digits) and "
        "DELTALLM_SALT_KEY (a new value).",
    )
    report.add(
        Severity.ACTION,
        "general_settings.database_url",
        "set DATABASE_URL to a NEW, empty PostgreSQL database. Do not use the LiteLLM "
        "database: both products use Prisma migrations.",
    )
    report.add(
        Severity.ACTION,
        "general_settings.model_deployment_bootstrap_from_config",
        "after the first successful start, set it to false. Then manage models in the Admin UI.",
    )
    if general.get("master_key") is not None:
        report.add(
            Severity.INFO,
            "general_settings.master_key",
            "application keys are not in this file. Create keys in DeltaLLM, or keep an "
            "existing key value with POST /ui/api/keys and the `key` field.",
        )


def _convert_guardrail(entry: object, path: str, report: ImportReport) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping) or not isinstance(entry.get("litellm_params"), Mapping):
        report.add(Severity.SKIPPED, path, "needs guardrail_name and litellm_params.")
        return None
    params: Mapping[str, Any] = entry["litellm_params"]
    kind = str(params.get("guardrail") or "")
    class_path = _GUARDRAIL_CLASSES.get(kind)
    mode = _GUARDRAIL_MODES.get(str(params.get("mode") or "pre_call"))
    if class_path is None or mode is None:
        report.add(Severity.SKIPPED, path, f"guardrail `{kind}` is not available in DeltaLLM.")
        return None
    converted: dict[str, Any] = {
        "guardrail": class_path,
        "mode": mode,
        "default_on": bool(params.get("default_on", False)),
        "default_action": "block",
    }
    if params.get("mode") == "during_call":
        report.add(Severity.CHANGED, path, "`during_call` became `pre_call`.")
    if kind == "presidio":
        _presidio_options(params, converted, path, report)
    else:
        converted["api_key"] = params.get("api_key") or "os.environ/LAKERA_API_KEY"
        flag_literal_secrets(report, path, [("api_key", converted["api_key"])])
    name = str(entry.get("guardrail_name") or kind)
    return {"guardrail_name": name, "deltallm_params": converted}


def _presidio_options(
    params: Mapping[str, Any], converted: dict[str, Any], path: str, report: ImportReport
) -> None:
    entities = params.get("pii_entities_config")
    if not isinstance(entities, Mapping) or not entities:
        converted["anonymize"] = True
        return
    converted["entities"] = [str(name) for name in entities]
    actions = {str(action).upper() for action in entities.values()}
    converted["anonymize"] = "BLOCK" not in actions
    if len(actions) > 1:
        report.add(
            Severity.CHANGED,
            path,
            "mixed MASK and BLOCK entities: DeltaLLM uses one action, so all entities block.",
        )
