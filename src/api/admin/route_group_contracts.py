from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RouteGroupMutationResponse(BaseModel):
    route_group_id: str
    group_key: str
    name: str | None
    mode: str
    routing_strategy: str | None
    enabled: bool
    member_count: int
    metadata: dict[str, Any] | None
    default_prompt: dict[str, str] | None = None
    owner_scope_type: str = "global"
    owner_scope_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class RouteGroupMemberMutationResponse(BaseModel):
    membership_id: str
    route_group_id: str
    deployment_id: str
    enabled: bool
    weight: int | None
    priority: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class RouteGroupDeleteResponse(BaseModel):
    deleted: bool
    warnings: list[str] = Field(default_factory=list)


class RoutePolicyResponse(BaseModel):
    route_policy_id: str
    route_group_id: str
    version: int
    semantics_version: int
    status: str
    policy_json: dict[str, Any]
    published_at: datetime | None
    published_by: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RoutePolicyMutationResponse(BaseModel):
    group_key: str
    policy: RoutePolicyResponse
    warnings: list[str] = Field(default_factory=list)


class RoutePolicyRollbackResponse(RoutePolicyMutationResponse):
    rolled_back_from_version: int
