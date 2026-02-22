from __future__ import annotations

import importlib
from typing import Any

from src.guardrails.base import CustomGuardrail, GuardrailAction, GuardrailMode


def _extract_guardrail_config(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    cfg = metadata.get("guardrails_config")
    if isinstance(cfg, dict):
        return cfg
    return None


def resolve_guardrail_names(
    global_defaults: list[str],
    org_metadata: dict[str, Any] | None = None,
    team_metadata: dict[str, Any] | None = None,
    key_metadata: dict[str, Any] | None = None,
    key_guardrails: list[str] | None = None,
) -> list[str]:
    names = set(global_defaults)

    org_cfg = _extract_guardrail_config(org_metadata)
    if org_cfg is not None:
        mode = org_cfg.get("mode", "inherit")
        if mode == "override":
            names = set(org_cfg.get("include", []))
        else:
            names |= set(org_cfg.get("include", []))
            names -= set(org_cfg.get("exclude", []))

    team_cfg = _extract_guardrail_config(team_metadata)
    if team_cfg is not None:
        mode = team_cfg.get("mode", "inherit")
        if mode == "override":
            names = set(team_cfg.get("include", []))
        else:
            names |= set(team_cfg.get("include", []))
            names -= set(team_cfg.get("exclude", []))

    key_cfg = _extract_guardrail_config(key_metadata)
    if key_cfg is not None:
        mode = key_cfg.get("mode", "inherit")
        if mode == "override":
            names = set(key_cfg.get("include", []))
        else:
            names |= set(key_cfg.get("include", []))
            names -= set(key_cfg.get("exclude", []))
    elif key_guardrails:
        names = set(key_guardrails)

    return sorted(names)


class GuardrailRegistry:
    def __init__(self) -> None:
        self._guardrails: dict[str, CustomGuardrail] = {}
        self._by_mode: dict[GuardrailMode, list[CustomGuardrail]] = {mode: [] for mode in GuardrailMode}

    def register(self, guardrail: CustomGuardrail) -> None:
        if guardrail.name in self._guardrails:
            self.unregister(guardrail.name)
        self._guardrails[guardrail.name] = guardrail
        self._by_mode[guardrail.mode].append(guardrail)

    def unregister(self, name: str) -> None:
        existing = self._guardrails.pop(name, None)
        if existing is not None:
            self._by_mode[existing.mode] = [g for g in self._by_mode[existing.mode] if g.name != name]

    def get(self, name: str) -> CustomGuardrail | None:
        return self._guardrails.get(name)

    def get_for_mode(self, mode: GuardrailMode) -> list[CustomGuardrail]:
        return self._by_mode[mode].copy()

    def get_default_guardrails(self) -> list[CustomGuardrail]:
        return [guardrail for guardrail in self._guardrails.values() if guardrail.default_on]

    def get_all_names(self) -> list[str]:
        return sorted(self._guardrails.keys())

    def get_for_key(
        self,
        key_data: dict[str, Any],
        override_guardrails: list[str] | None = None,
    ) -> list[CustomGuardrail]:
        if override_guardrails is not None:
            return [self._guardrails[name] for name in override_guardrails if name in self._guardrails]

        global_defaults = [g.name for g in self.get_default_guardrails()]

        org_metadata = key_data.get("org_metadata")
        team_metadata = key_data.get("team_metadata")
        key_metadata = key_data.get("metadata")

        raw_key_guardrails = key_data.get("guardrails")
        key_guardrails = [str(n) for n in raw_key_guardrails] if isinstance(raw_key_guardrails, list) and raw_key_guardrails else None

        has_scoped_config = (
            _extract_guardrail_config(org_metadata) is not None
            or _extract_guardrail_config(team_metadata) is not None
            or _extract_guardrail_config(key_metadata) is not None
            or key_guardrails is not None
        )

        if not has_scoped_config:
            return self.get_default_guardrails()

        resolved_names = resolve_guardrail_names(
            global_defaults=global_defaults,
            org_metadata=org_metadata,
            team_metadata=team_metadata,
            key_metadata=key_metadata,
            key_guardrails=key_guardrails,
        )

        return [self._guardrails[name] for name in resolved_names if name in self._guardrails]

    def load_from_config(self, config: list[Any]) -> None:
        for guardrail_config in config:
            item = guardrail_config.model_dump(mode="python") if hasattr(guardrail_config, "model_dump") else dict(guardrail_config)
            name = item["guardrail_name"]
            params = dict(item.get("litellm_params") or {})

            class_path = params.pop("guardrail", None)
            if not class_path:
                raise ValueError(f"Guardrail '{name}' is missing litellm_params.guardrail")

            mode = GuardrailMode(params.pop("mode", GuardrailMode.PRE_CALL))
            action = GuardrailAction(params.pop("default_action", GuardrailAction.BLOCK))
            default_on = bool(params.pop("default_on", True))

            guardrail_cls = self._import_class(class_path)
            instance = guardrail_cls(name=name, mode=mode, default_on=default_on, action=action, **params)
            if not isinstance(instance, CustomGuardrail):
                raise TypeError(f"Guardrail '{class_path}' must inherit CustomGuardrail")
            self.register(instance)

    @staticmethod
    def _import_class(class_path: str) -> type:
        module_path, class_name = class_path.rsplit(".", 1)
        module_candidates = [module_path]
        if module_path.startswith("deltallm."):
            module_candidates.append(module_path.replace("deltallm.", "src.", 1))

        last_err: Exception | None = None
        for candidate in module_candidates:
            try:
                module = importlib.import_module(candidate)
                return getattr(module, class_name)
            except Exception as exc:
                last_err = exc

        raise ImportError(f"Could not import guardrail class '{class_path}': {last_err}") from last_err


guardrail_registry = GuardrailRegistry()
