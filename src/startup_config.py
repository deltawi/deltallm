"""The single initial file/environment resolution shared with the launcher."""

from dataclasses import dataclass
from typing import Any

from src.config import AppConfig, Settings, get_settings
from src.config_runtime.loader import build_app_config, load_yaml_dict
from src.config_runtime.secrets import SecretResolver
from src.lifecycle_settings import LifecycleSettings, resolve_lifecycle_settings


@dataclass(frozen=True)
class StartupConfig:
    settings: Settings
    file_config: dict[str, Any]
    app_config: AppConfig
    lifecycle: LifecycleSettings

    @classmethod
    def load(cls) -> "StartupConfig":
        settings = get_settings()
        file_config = load_yaml_dict(settings.config_path)
        config = build_app_config(file_config, secret_resolver=SecretResolver())
        return cls(
            settings,
            file_config,
            config,
            resolve_lifecycle_settings(config.general_settings, settings),
        )

    def validate_effective(self, config: AppConfig) -> None:
        if resolve_lifecycle_settings(config.general_settings, self.settings) != self.lifecycle:
            raise RuntimeError(
                "Lifecycle settings must match startup file/environment configuration"
            )
