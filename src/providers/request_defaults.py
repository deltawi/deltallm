"""The shared projection of configured defaults actually forwarded to providers."""

from collections.abc import Mapping

_UI_ONLY_DEFAULT_KEYS = frozenset({"available_voices"})


def provider_request_defaults(model_info: Mapping[str, object]) -> dict[str, object]:
    # Compatibility boundary for the existing dynamic provider-parameter contract.
    defaults = model_info.get("default_params")
    if not isinstance(defaults, dict):
        return {}
    return {key: value for key, value in defaults.items() if key not in _UI_ONLY_DEFAULT_KEYS}
