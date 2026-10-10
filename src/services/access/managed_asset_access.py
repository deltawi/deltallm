from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from time import monotonic
from typing import Awaitable, Callable, TypeVar


_ResourceT = TypeVar("_ResourceT")
MANAGED_ASSET_RESOURCE_BATCH_SIZE = 500


@dataclass(slots=True)
class AuthorizationSnapshotFreshness:
    """Shared mutable guard held by both a service and its published generation."""

    max_staleness_seconds: float = 60.0
    reload_failed_at: float | None = None

    def mark_reload_failed(self) -> None:
        if self.reload_failed_at is None:
            self.reload_failed_at = monotonic()

    @property
    def expired(self) -> bool:
        return bool(
            self.reload_failed_at is not None
            and monotonic() - self.reload_failed_at >= self.max_staleness_seconds
        )

    @property
    def ready(self) -> bool:
        return not self.expired


class AssetKind(str, Enum):
    MODEL = "model"
    ROUTE_GROUP = "route_group"
    MCP_SERVER = "mcp_server"
    PROMPT_TEMPLATE = "prompt_template"
    NAMED_CREDENTIAL = "named_credential"


class GovernanceSource(str, Enum):
    PLATFORM = "platform"
    CREATOR = "creator"


class AssetVisibility(str, Enum):
    PRIVATE = "private"
    TEAM = "team"
    ORGANIZATION = "organization"
    PUBLIC = "public"
    SHARED = "shared"


class AssetAccessRole(str, Enum):
    READER = "reader"
    EDITOR = "editor"
    OWNER = "owner"


class AssetSubjectType(str, Enum):
    TEAM = "team"
    ORGANIZATION = "organization"
    PUBLIC = "public"


class ModelCredentialBindingMode(str, Enum):
    """How a model is authorized to use a named credential.

    Owner-delegated bindings deliberately authorize the model, not its readers or
    editors. Audience-scoped bindings retain the older rule for credentials that
    the actor can use but does not own.
    """

    AUDIENCE_SCOPED = "audience_scoped"
    OWNER_DELEGATED = "owner_delegated"
    PLATFORM_OVERRIDE = "platform_override"


class ModelCredentialBindingState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ManagedAsset:
    asset_id: str
    asset_kind: AssetKind
    governance_source: GovernanceSource
    owner_account_id: str | None
    policy_version: int = 1
    state: str = "active"

    def __post_init__(self) -> None:
        if not self.asset_id.strip():
            raise ValueError("asset_id is required")
        if self.governance_source is GovernanceSource.CREATOR and not self.owner_account_id:
            raise ValueError("creator-governed assets require an owner account")
        if self.policy_version < 1:
            raise ValueError("policy_version must be positive")
        if self.state not in {"active", "archived"}:
            raise ValueError("unsupported managed asset state")


@dataclass(frozen=True, slots=True)
class AssetGrant:
    managed_asset_id: str
    subject_type: AssetSubjectType
    access_role: AssetAccessRole
    subject_id: str | None = None

    def __post_init__(self) -> None:
        if not self.managed_asset_id.strip():
            raise ValueError("managed_asset_id is required")
        if self.access_role is AssetAccessRole.OWNER:
            raise ValueError("owner is implicit from the managed asset and cannot be granted")
        if self.subject_type is AssetSubjectType.PUBLIC:
            if self.subject_id is not None:
                raise ValueError("public grants cannot have a subject_id")
            if self.access_role is not AssetAccessRole.READER:
                raise ValueError("public visibility is reader-only")
        elif not self.subject_id:
            raise ValueError("team and organization grants require a subject_id")

    @property
    def visibility(self) -> AssetVisibility:
        return AssetVisibility(self.subject_type.value)


@dataclass(frozen=True, slots=True, init=False)
class AssetAccessPolicy:
    asset: ManagedAsset
    grants: tuple[AssetGrant, ...]

    def __init__(
        self,
        asset: ManagedAsset,
        grants: tuple[AssetGrant, ...] = (),
        *,
        grant: AssetGrant | None = None,
    ) -> None:
        """Build a policy from its audience grants.

        ``grant`` remains accepted while callers migrate from the original one-audience
        policy. New code must use ``grants``; passing both is rejected rather than silently
        dropping access rules.
        """

        if grant is not None:
            if grants:
                raise ValueError("grant and grants cannot both be provided")
            grants = (grant,)
        normalized = tuple(grants)
        seen_subjects: set[tuple[AssetSubjectType, str | None]] = set()
        for item in normalized:
            if item.managed_asset_id != asset.asset_id:
                raise ValueError("grant must belong to the managed asset")
            subject = (item.subject_type, item.subject_id)
            if subject in seen_subjects:
                raise ValueError("an audience can only be granted access once")
            seen_subjects.add(subject)
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "grants", normalized)

    @property
    def grant(self) -> AssetGrant | None:
        """Compatibility accessor for callers that only understand one grant."""

        if len(self.grants) > 1:
            raise ValueError("policy has multiple grants; use grants")
        return self.grants[0] if self.grants else None

    @property
    def visibility(self) -> AssetVisibility:
        if not self.grants:
            return AssetVisibility.PRIVATE
        subject_types = {grant.subject_type for grant in self.grants}
        if len(subject_types) == 1:
            return AssetVisibility(next(iter(subject_types)).value)
        return AssetVisibility.SHARED


@dataclass(frozen=True, slots=True)
class AssetPrincipal:
    account_id: str | None
    team_ids: frozenset[str] = frozenset()
    organization_ids: frozenset[str] = frozenset()
    is_platform_admin: bool = False


@dataclass(frozen=True, slots=True)
class AssetCapabilities:
    role: AssetAccessRole | None
    can_read: bool
    can_write: bool
    can_manage_access: bool
    can_delete: bool
    is_platform_admin_override: bool = False


_NO_ACCESS = AssetCapabilities(
    role=None,
    can_read=False,
    can_write=False,
    can_manage_access=False,
    can_delete=False,
)


def resolve_asset_capabilities(
    policy: AssetAccessPolicy,
    principal: AssetPrincipal,
) -> AssetCapabilities:
    """Resolve one immutable policy snapshot without performing I/O.

    Platform administrators retain an audited break-glass override, but the override
    never changes the asset's owner role. The creator is the only implicit owner.
    """

    if principal.is_platform_admin:
        return AssetCapabilities(
            role=None,
            can_read=True,
            can_write=True,
            can_manage_access=True,
            can_delete=True,
            is_platform_admin_override=True,
        )

    if policy.asset.owner_account_id and principal.account_id == policy.asset.owner_account_id:
        return _capabilities_for_role(AssetAccessRole.OWNER)

    matching_roles = [
        grant.access_role for grant in policy.grants if _grant_matches(grant, principal)
    ]
    if not matching_roles:
        return _NO_ACCESS
    role = (
        AssetAccessRole.EDITOR
        if AssetAccessRole.EDITOR in matching_roles
        else AssetAccessRole.READER
    )
    return _capabilities_for_role(role)


def authorize_model_credential_binding(
    credential_policy: AssetAccessPolicy,
    principal: AssetPrincipal,
) -> ModelCredentialBindingMode:
    """Authorize a principal to bind a credential without granting its audience access.

    Credential owners delegate opaque runtime use to the model. A principal who
    only receives credential access may still attach it, but the model audience
    must remain inside the credential audience. Platform administrators retain
    their existing audited override.
    """

    if credential_policy.asset.asset_kind is not AssetKind.NAMED_CREDENTIAL:
        raise ValueError("model credential policy has the wrong asset kind")
    capabilities = resolve_asset_capabilities(credential_policy, principal)
    if capabilities.is_platform_admin_override:
        return ModelCredentialBindingMode.PLATFORM_OVERRIDE
    if capabilities.role is AssetAccessRole.OWNER:
        return ModelCredentialBindingMode.OWNER_DELEGATED
    if capabilities.can_read:
        return ModelCredentialBindingMode.AUDIENCE_SCOPED
    raise PermissionError("named credential is invalid or inaccessible")


def binding_requires_audience_coverage(mode: ModelCredentialBindingMode | str | None) -> bool:
    return mode == ModelCredentialBindingMode.AUDIENCE_SCOPED or str(mode or "") == (
        ModelCredentialBindingMode.AUDIENCE_SCOPED.value
    )


def revise_asset_access(
    policy: AssetAccessPolicy,
    principal: AssetPrincipal,
    *,
    grants: tuple[AssetGrant, ...] | None = None,
    visibility: AssetVisibility | None = None,
    access_role: AssetAccessRole | None = None,
    subject_id: str | None = None,
) -> AssetAccessPolicy:
    """Return a validated replacement policy after checking management authority."""

    capabilities = resolve_asset_capabilities(policy, principal)
    if not capabilities.can_manage_access:
        raise PermissionError("only the owner or a platform administrator can manage asset access")

    if grants is None:
        if visibility is None:
            raise ValueError("grants are required")
        if visibility is AssetVisibility.SHARED:
            raise ValueError("shared visibility must be expressed as grants")
        if visibility is AssetVisibility.PRIVATE:
            if access_role is not None or subject_id is not None:
                raise ValueError("private visibility cannot have an audience role or subject")
            grants = ()
        else:
            subject_type = AssetSubjectType(visibility.value)
            if visibility is AssetVisibility.PUBLIC:
                if subject_id is not None:
                    raise ValueError("public visibility cannot have a subject")
                access_role = access_role or AssetAccessRole.READER
            elif access_role is None:
                raise ValueError("team and organization visibility require an access role")
            grants = (
                AssetGrant(
                    managed_asset_id=policy.asset.asset_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    access_role=access_role,
                ),
            )

    for grant in grants:
        if grant.managed_asset_id != policy.asset.asset_id:
            raise ValueError("grant must belong to the managed asset")
        if grant.subject_type is AssetSubjectType.PUBLIC and not principal.is_platform_admin:
            raise PermissionError("only a platform administrator can set public visibility")

    return AssetAccessPolicy(asset=policy.asset, grants=grants)


def validate_grant_subject_for_principal(
    policy: AssetAccessPolicy,
    principal: AssetPrincipal,
) -> None:
    """Prevent owners from sharing into teams or organizations they do not belong to."""

    if principal.is_platform_admin:
        return
    for grant in policy.grants:
        if (
            grant.subject_type is AssetSubjectType.TEAM
            and grant.subject_id not in principal.team_ids
        ):
            raise PermissionError("asset can only be shared with one of the owner's teams")
        if (
            grant.subject_type is AssetSubjectType.ORGANIZATION
            and grant.subject_id not in principal.organization_ids
        ):
            raise PermissionError("asset can only be shared with one of the owner's organizations")


def validate_model_credential_audience(
    model_policy: AssetAccessPolicy,
    credential_policy: AssetAccessPolicy,
    *,
    model_owner_principal: AssetPrincipal,
    model_team_organization_id: str | None = None,
    model_team_organization_ids: dict[str, str | None] | None = None,
) -> None:
    """Require a creator model's complete audience to be able to read its credential.

    The check is structural rather than a one-time check for the actor attaching the
    credential. That prevents a model from becoming a credential-sharing side door
    when either policy is changed later.
    """

    if model_policy.asset.governance_source is not GovernanceSource.CREATOR:
        return
    if credential_policy.asset.asset_kind is not AssetKind.NAMED_CREDENTIAL:
        raise ValueError("model credential policy has the wrong asset kind")
    if not resolve_asset_capabilities(credential_policy, model_owner_principal).can_read:
        raise ValueError("model owner cannot read the selected named credential")

    credential_grants = credential_policy.grants
    has_public_credential = any(
        grant.subject_type is AssetSubjectType.PUBLIC for grant in credential_grants
    )
    team_organizations = dict(model_team_organization_ids or {})
    if model_team_organization_id is not None:
        for grant in model_policy.grants:
            if grant.subject_type is AssetSubjectType.TEAM and grant.subject_id:
                team_organizations.setdefault(grant.subject_id, model_team_organization_id)

    for model_grant in model_policy.grants:
        if has_public_credential:
            continue
        if model_grant.subject_type is AssetSubjectType.PUBLIC:
            raise ValueError("a public model requires a public named credential")
        if model_grant.subject_type is AssetSubjectType.ORGANIZATION:
            if any(
                grant.subject_type is AssetSubjectType.ORGANIZATION
                and grant.subject_id == model_grant.subject_id
                for grant in credential_grants
            ):
                continue
            raise ValueError("named credential visibility does not cover the model organization")
        if model_grant.subject_type is AssetSubjectType.TEAM:
            if any(
                grant.subject_type is AssetSubjectType.TEAM
                and grant.subject_id == model_grant.subject_id
                for grant in credential_grants
            ):
                continue
            team_organization_id = team_organizations.get(str(model_grant.subject_id or ""))
            if team_organization_id and any(
                grant.subject_type is AssetSubjectType.ORGANIZATION
                and grant.subject_id == team_organization_id
                for grant in credential_grants
            ):
                continue
            raise ValueError("named credential visibility does not cover the model team")


async def load_managed_asset_resources_in_batches(
    asset_ids: list[str],
    loader: Callable[[list[str]], Awaitable[list[_ResourceT]]],
) -> list[_ResourceT]:
    """Load snapshot resources without producing an unbounded SQL bind list."""

    resources: list[_ResourceT] = []
    for start in range(0, len(asset_ids), MANAGED_ASSET_RESOURCE_BATCH_SIZE):
        resources.extend(await loader(asset_ids[start : start + MANAGED_ASSET_RESOURCE_BATCH_SIZE]))
    return resources


def namespace_creator_callable_key(key: str, principal: AssetPrincipal) -> str:
    """Give every creator a collision-proof runtime namespace.

    Platform keys remain unchanged for compatibility. Creator keys are canonicalized
    on creation, so a user cannot squat on another creator's key or on a future
    platform key.
    """

    normalized = str(key or "").strip()
    has_creator_namespace = bool(
        len(normalized) > 73
        and normalized.startswith("creator-")
        and normalized[72] == "-"
        and all(character in "0123456789abcdef" for character in normalized[8:72])
    )
    if principal.is_platform_admin:
        if has_creator_namespace:
            raise PermissionError("creator callable-key namespaces are reserved")
        return normalized
    if not principal.account_id:
        raise PermissionError("authenticated account is required")
    namespace = sha256(principal.account_id.encode("utf-8")).hexdigest()
    prefix = f"creator-{namespace}-"
    if normalized.startswith(prefix):
        return normalized
    if has_creator_namespace:
        raise PermissionError("creator key belongs to a different account namespace")
    return f"{prefix}{normalized}"


def serialize_asset_access(
    policy: AssetAccessPolicy,
    principal: AssetPrincipal,
) -> dict[str, object]:
    capabilities = resolve_asset_capabilities(policy, principal)
    return {
        "managed_asset_id": policy.asset.asset_id,
        "asset_kind": policy.asset.asset_kind.value,
        "governance_source": policy.asset.governance_source.value,
        "owner_account_id": policy.asset.owner_account_id,
        "visibility": policy.visibility.value,
        "subject_id": policy.grants[0].subject_id if len(policy.grants) == 1 else None,
        "access_role": policy.grants[0].access_role.value if len(policy.grants) == 1 else None,
        "grants": [
            {
                "subject_type": grant.subject_type.value,
                "subject_id": grant.subject_id,
                "access_role": grant.access_role.value,
            }
            for grant in policy.grants
        ],
        "effective_role": capabilities.role.value if capabilities.role is not None else None,
        "policy_version": policy.asset.policy_version,
        "capabilities": {
            "read": capabilities.can_read,
            "write": capabilities.can_write,
            "manage_access": capabilities.can_manage_access,
            "delete": capabilities.can_delete,
            "platform_admin_override": capabilities.is_platform_admin_override,
        },
    }


def _grant_matches(grant: AssetGrant, principal: AssetPrincipal) -> bool:
    if grant.subject_type is AssetSubjectType.PUBLIC:
        return True
    if grant.subject_type is AssetSubjectType.TEAM:
        return grant.subject_id in principal.team_ids
    return grant.subject_id in principal.organization_ids


def _capabilities_for_role(role: AssetAccessRole) -> AssetCapabilities:
    can_write = role in {AssetAccessRole.EDITOR, AssetAccessRole.OWNER}
    is_owner = role is AssetAccessRole.OWNER
    return AssetCapabilities(
        role=role,
        can_read=True,
        can_write=can_write,
        can_manage_access=is_owner,
        can_delete=is_owner,
    )
