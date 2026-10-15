from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.auth.roles import validate_organization_role, validate_team_role

MAX_SSO_GROUP_MAPPINGS = 200
MAX_SSO_GROUP_VALUE_LENGTH = 256
_CLAIM_PATH = re.compile(r"^[A-Za-z0-9_:\-]+(\.[A-Za-z0-9_:\-]+)*$")


def _normalize_value(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("claim values cannot be blank")
    if len(normalized) > MAX_SSO_GROUP_VALUE_LENGTH:
        raise ValueError(f"claim values must be at most {MAX_SSO_GROUP_VALUE_LENGTH} characters")
    return normalized


class SSOGroupMapping(BaseModel):
    """Grant one team or organization role to accounts whose claim has `value`."""

    model_config = ConfigDict(extra="forbid")

    value: str
    team_id: str | None = None
    team_role: str | None = None
    organization_id: str | None = None
    org_role: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: object) -> str:
        return _normalize_value(value)

    @field_validator("team_id", "organization_id", mode="before")
    @classmethod
    def normalize_target(cls, value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_target(self) -> "SSOGroupMapping":
        if (self.team_id is None) == (self.organization_id is None):
            raise ValueError("each mapping needs exactly one of team_id or organization_id")
        if self.team_id is not None:
            if self.org_role is not None:
                raise ValueError("a team mapping uses team_role, not org_role")
            self.team_role = validate_team_role(self.team_role)
        else:
            if self.team_role is not None:
                raise ValueError("an organization mapping uses org_role, not team_role")
            self.org_role = validate_organization_role(self.org_role)
        return self


class SSOGroupSyncSettings(BaseModel):
    """Map values of one SSO token claim to platform admin, organizations, and teams."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    claim: str = "groups"
    admin_values: list[str] = Field(default_factory=list)
    require_mapped_value: bool = False
    remove_unlisted: bool = False
    mappings: list[SSOGroupMapping] = Field(default_factory=list, max_length=MAX_SSO_GROUP_MAPPINGS)

    @field_validator("claim", mode="before")
    @classmethod
    def normalize_claim(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not _CLAIM_PATH.fullmatch(normalized) or len(normalized) > 128:
            raise ValueError("claim must be a claim name or a dot path such as realm_access.roles")
        return normalized

    @field_validator("admin_values", mode="before")
    @classmethod
    def normalize_admin_values(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("admin_values must be a list")
        result: list[str] = []
        for item in value:
            normalized = _normalize_value(item)
            if normalized not in result:
                result.append(normalized)
        return result

    @model_validator(mode="after")
    def validate_values(self) -> "SSOGroupSyncSettings":
        admin = set(self.admin_values)
        seen: set[tuple[str, str]] = set()
        for mapping in self.mappings:
            if mapping.value in admin:
                raise ValueError(f"claim value {mapping.value!r} is already an admin value")
            target = mapping.team_id or f"org:{mapping.organization_id}"
            key = (mapping.value, target)
            if key in seen:
                raise ValueError(f"claim value {mapping.value!r} maps to the same target twice")
            seen.add(key)
        return self

    @property
    def mapped_team_ids(self) -> frozenset[str]:
        return frozenset(item.team_id for item in self.mappings if item.team_id)

    @property
    def mapped_organization_ids(self) -> frozenset[str]:
        return frozenset(item.organization_id for item in self.mappings if item.organization_id)
