"""Keep the suite tier in step with every callable target on the gateway.

Tier names are the names callers use (model names and route group keys), not
provider model IDs. A tier that lists provider IDs answers 403 for each call.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from scripts.live_smoke.config import SUITE_MARKER, ResourceNames
from scripts.live_smoke.gateway_client import GatewayClient, GatewayError

_PAGE_LIMIT = 100


@dataclass(frozen=True)
class CallableTarget:
    key: str
    target_type: str
    mode: str | None
    mode_conflict: bool = False
    # Created by an account and not public: only its owner and the teams or
    # organizations it is shared with can call it. Tiers and grants do not change this.
    creator_scoped: bool = False


@dataclass(frozen=True)
class TierSyncResult:
    tier_id: str
    created: bool
    activated_version_id: str | None
    added: tuple[str, ...]
    removed: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    targets: list[CallableTarget]
    source: str


def _pages(admin: GatewayClient, path: str, action: str, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = admin.json("GET", path, action, params={"limit": limit, "offset": offset})
        items.extend(page.get("data") or [])
        if not (page.get("pagination") or {}).get("has_more"):
            return items
        offset += limit


def _catalog_from_admin_lists(
    models: list[dict[str, Any]], groups: list[dict[str, Any]]
) -> list[CallableTarget]:
    """Build the catalog the way the gateway does: model names, then enabled route groups.

    Releases before the callable-target catalog fix return an empty
    /ui/api/callable-targets list, so the suite falls back to this.
    """

    modes: dict[str, set[str]] = {}
    for deployment in models:
        info = deployment.get("model_info") or {}
        mode = str(info.get("mode") or deployment.get("mode") or "").strip().lower()
        modes.setdefault(str(deployment["model_name"]), set()).update({mode} if mode else set())
    catalog = {
        name: CallableTarget(
            key=name,
            target_type="model",
            mode=next(iter(found)) if len(found) == 1 else None,
            mode_conflict=len(found) > 1,
        )
        for name, found in modes.items()
    }
    for group in groups:
        if group.get("enabled", True):
            key = str(group["group_key"])
            catalog[key] = CallableTarget(key, "route_group", group.get("mode") or "chat")
    return list(catalog.values())


def _creator_scoped_keys(models: list[dict[str, Any]], groups: list[dict[str, Any]]) -> set[str]:
    """Names of creator assets that are not public (see CallableTarget.creator_scoped)."""

    scoped: set[str] = set()
    public: set[str] = set()
    items = [(item.get("model_name"), item) for item in models]
    items += [(item.get("group_key"), item) for item in groups]
    for key, item in items:
        access = item.get("access")
        if not key or not isinstance(access, dict):
            continue
        if access.get("governance_source") != "creator":
            continue
        grants = access.get("grants") or []
        if any(isinstance(g, dict) and g.get("subject_type") == "public" for g in grants):
            public.add(str(key))
        else:
            scoped.add(str(key))
    return scoped - public


def load_catalog(admin: GatewayClient) -> Catalog:
    models = _pages(admin, "/ui/api/models", "list models", 500)
    groups = _pages(admin, "/ui/api/route-groups", "list route groups", 200)
    targets = [
        CallableTarget(
            key=str(item["callable_key"]),
            target_type=str(item.get("target_type") or "model"),
            mode=item.get("mode"),
            mode_conflict=bool(item.get("mode_conflict")),
        )
        for item in _pages(admin, "/ui/api/callable-targets", "list callable targets", 500)
    ]
    source = "callable targets"
    if not targets:
        targets = _catalog_from_admin_lists(models, groups)
        source = "model and route group lists (callable targets list was empty)"
    scoped = _creator_scoped_keys(models, groups)
    targets = [replace(target, creator_scoped=target.key in scoped) for target in targets]
    return Catalog(sorted(targets, key=lambda target: target.key), source)


def find_tier(admin: GatewayClient, tier_key: str) -> dict[str, Any] | None:
    page = admin.json(
        "GET",
        "/ui/api/tiers",
        "find suite tier",
        params={"search": tier_key, "limit": 200},
    )
    for tier in page.get("data") or []:
        if tier.get("tier_key") == tier_key:
            return tier
    return None


def _ensure_tier(admin: GatewayClient, names: ResourceNames) -> tuple[dict[str, Any], bool]:
    existing = find_tier(admin, names.tier_key)
    if existing is not None:
        return existing, False
    created = admin.json(
        "POST",
        "/ui/api/tiers",
        "create suite tier",
        json={
            "tier_key": names.tier_key,
            "name": names.tier_name,
            "description": "Gives the live smoke suite access to every model.",
            "enabled": True,
            "metadata": {"managed_by": SUITE_MARKER},
        },
    )
    return created, True


def _list_policies(admin: GatewayClient, tier_id: str, version_id: str) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = admin.json(
            "GET",
            f"/ui/api/tiers/{tier_id}/versions/{version_id}/model-policies",
            "list tier model policies",
            params={"limit": _PAGE_LIMIT, "offset": offset},
        )
        policies.extend(page.get("data") or [])
        if not (page.get("pagination") or {}).get("has_more"):
            return policies
        offset += _PAGE_LIMIT


def _allowed_keys(policies: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(policy["callable_key"])
        for policy in policies
        if policy.get("enabled", True) and policy.get("access_mode", "allow") == "allow"
    }


def _draft_version(admin: GatewayClient, tier: dict[str, Any]) -> tuple[str, int]:
    draft = tier.get("latest_draft_version")
    if draft:
        return str(draft["tier_version_id"]), int(draft["configuration_revision"])
    created = admin.json(
        "POST",
        f"/ui/api/tiers/{tier['tier_id']}/versions",
        "create tier draft",
        json={"metadata": {"managed_by": SUITE_MARKER}},
    )
    return str(created["tier_version_id"]), int(created["configuration_revision"])


def _fill_draft(
    admin: GatewayClient,
    tier_id: str,
    version_id: str,
    revision: int,
    desired: set[str],
) -> int:
    """Make the draft allow exactly the desired keys. Return the new configuration revision."""
    base = f"/ui/api/tiers/{tier_id}/versions/{version_id}/model-policies"
    policies = {
        str(item["callable_key"]): item for item in _list_policies(admin, tier_id, version_id)
    }
    for key, policy in sorted(policies.items()):
        if key in desired:
            if key in _allowed_keys([policy]):
                continue
            response = admin.json(
                "PATCH",
                f"{base}/{policy['tier_model_policy_id']}",
                f"allow {key} in tier draft",
                json={"expected_revision": revision, "enabled": True, "access_mode": "allow"},
            )
        else:
            response = admin.json(
                "DELETE",
                f"{base}/{policy['tier_model_policy_id']}",
                f"remove {key} from tier draft",
                json={"expected_revision": revision},
            )
        revision = int(response["configuration_revision"])
    for key in sorted(desired - set(policies)):
        response = admin.json(
            "POST",
            base,
            f"add {key} to tier draft",
            json={"expected_revision": revision, "callable_key": key, "access_mode": "allow"},
        )
        revision = int(response["configuration_revision"])
    return revision


def sync_tier(
    admin: GatewayClient, names: ResourceNames, callable_keys: Iterable[str]
) -> TierSyncResult:
    """Make the active tier version allow every key. Activate a new version only on change."""

    desired = set(callable_keys)
    if not desired:
        raise GatewayError("sync suite tier", 0, "the gateway has no callable models")
    tier, created = _ensure_tier(admin, names)
    tier_id = str(tier["tier_id"])
    detail = admin.json(
        "GET",
        f"/ui/api/tiers/{tier_id}",
        "read suite tier",
        params={"include_versions": "false"},
    )["tier"]
    active_id = detail.get("active_version_id")
    live = _allowed_keys(_list_policies(admin, tier_id, active_id)) if active_id else set()
    if active_id and desired <= live:
        return TierSyncResult(tier_id, created, None, (), ())

    version_id, revision = _draft_version(admin, detail)
    revision = _fill_draft(admin, tier_id, version_id, revision, desired)
    admin.json(
        "POST",
        f"/ui/api/tiers/{tier_id}/versions/{version_id}/activate",
        "activate suite tier version",
        json={"expected_revision": revision, "expected_active_version_id": active_id},
    )
    added, removed = sorted(desired - live), sorted(live - desired)
    return TierSyncResult(tier_id, created, version_id, tuple(added), tuple(removed))
