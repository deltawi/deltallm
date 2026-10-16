from __future__ import annotations

from src.db.routing.route_policy_lifecycle import (
    RoutePolicyLifecycleMixin,
    RoutePolicyRecord,
    to_policy_record,
)


class RoutePolicyDraftMixin(RoutePolicyLifecycleMixin):
    """Draft operations that do not change the published policy."""

    async def discard_draft_policy(self, group_key: str) -> RoutePolicyRecord | None:
        """Delete the group's draft and return it, or return None when there is no draft."""
        if self.prisma is None:
            return None
        self.require_transactions("discard_draft_policy")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._discard_draft_policy_in_tx(group_key)

    async def _discard_draft_policy_in_tx(self, group_key: str) -> RoutePolicyRecord | None:
        group_id = await self._lock_group_id(group_key)
        if group_id is None:
            return None
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND status = 'draft'
            RETURNING route_policy_id, route_group_id, version, semantics_version, status, policy_json,
                      published_at, published_by, created_at, updated_at
            """,
            group_id,
        )
        if not rows:
            return None
        latest = max(rows, key=lambda row: int(row.get("version") or 0))
        return to_policy_record(latest)
