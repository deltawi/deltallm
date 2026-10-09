from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict


class ListPagination(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class AssetListItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    created_by_user_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    visibility: str | None = None


class ModelListItem(AssetListItem):
    deployment_id: str
    model_name: str
    provider: str
    healthy: bool | None = None
    health_status: Literal["healthy", "unhealthy", "unknown"]


class GroupListItem(AssetListItem):
    route_group_id: str
    group_key: str
    name: str | None
    mode: str
    routing_strategy: str | None
    enabled: bool
    member_count: int
    health_status: (
        Literal["healthy", "degraded", "unhealthy", "unknown", "paused", "empty"] | None
    ) = None
    active_member_count: int | None = None
    healthy_member_count: int | None = None


class PromptListItem(AssetListItem):
    prompt_template_id: str
    template_key: str
    name: str
    description: str | None
    version_count: int
    label_count: int
    binding_count: int


Item = TypeVar("Item", bound=AssetListItem)


class AdminListResponse(BaseModel, Generic[Item]):
    data: list[Item]
    pagination: ListPagination
