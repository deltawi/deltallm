from __future__ import annotations

import importlib
from typing import Any

from src.guardrails.base import CustomGuardrail, GuardrailAction, GuardrailMode


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

    def get_for_key(
        self,
        key_data: dict[str, Any],
        override_guardrails: list[str] | None = None,
    ) -> list[CustomGuardrail]:
        selected_names: list[str] | None = None
        if override_guardrails is not None:
            selected_names = override_guardrails
        else:
            raw = key_data.get("guardrails")
            if isinstance(raw, list) and raw:
                selected_names = [str(name) for name in raw]

        if selected_names is None:
            return self.get_default_guardrails()
        return [self._guardrails[name] for name in selected_names if name in self._guardrails]

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
            except Exception as exc:  # pragma: no cover
                last_err = exc

        raise ImportError(f"Could not import guardrail class '{class_path}': {last_err}") from last_err


guardrail_registry = GuardrailRegistry()
