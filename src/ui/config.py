from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UIMountSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mount_path: str = Field(default="", max_length=128, pattern=r"^(/[A-Za-z0-9_-]+)*$")
    external_console: bool = False

    @model_validator(mode="after")
    def require_console_mount(self) -> UIMountSettings:
        if self.external_console and not self.mount_path:
            raise ValueError("Console UI requires a separate mount path")
        return self
