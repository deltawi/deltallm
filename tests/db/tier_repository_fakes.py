from __future__ import annotations


class _FakePrisma:
    def __init__(
        self,
        *,
        enable_tx: bool = False,
        overlap_count: int = 0,
        fail_on_sql: str | None = None,
        assignment_version_tier_id: str | None = "tier-1",
        assignment_version_status: str = "active",
        assignment_tier_exists: bool = True,
        mutation_version_status: str = "draft",
        version_lookup_status: str = "draft",
        unpinned_assignment_count: int = 0,
        pinned_assignment_count: int = 0,
        active_assignment_count: int = 0,
        current_active_version_id: str | None = "ver-active",
        assignment_rows: dict[str, dict[str, object]] | None = None,
        organization_exists: bool = True,
    ) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.enable_tx = enable_tx
        self.overlap_count = overlap_count
        self.fail_on_sql = fail_on_sql
        self.assignment_version_tier_id = assignment_version_tier_id
        self.assignment_version_status = assignment_version_status
        self.assignment_tier_exists = assignment_tier_exists
        self.mutation_version_status = mutation_version_status
        self.version_lookup_status = version_lookup_status
        self.unpinned_assignment_count = unpinned_assignment_count
        self.pinned_assignment_count = pinned_assignment_count
        self.active_assignment_count = active_assignment_count
        self.current_active_version_id = current_active_version_id
        self.assignment_rows = dict(assignment_rows or {})
        self.organization_exists = organization_exists
        self.tx_clients: list[_FakePrisma] = []
        self.tx_started = 0
        self.tx_committed = 0
        self.tx_rolled_back = 0
        if enable_tx:
            self.tx = lambda: _FakeTxContext(self)

    async def query_raw(self, sql: str, *params: object) -> list[dict[str, object]]:
        self.calls.append((sql, params))
        if self.fail_on_sql and self.fail_on_sql in sql:
            raise RuntimeError("simulated query failure")
        if "v.tier_id AS version_tier_id" in sql:
            if self.assignment_version_tier_id is None:
                return []
            return [
                {
                    "version_tier_id": self.assignment_version_tier_id,
                    "status": self.assignment_version_status,
                }
            ]
        if "SELECT status" in sql and "FROM deltallm_tierversion" in sql:
            return [{"status": self.mutation_version_status}]
        if "pg_advisory_xact_lock" in sql:
            return [{"locked": None}]
        if "FROM deltallm_tier\n" in sql and "WHERE tier_id = $1" in sql:
            return [{"tier_id": params[0]}] if self.assignment_tier_exists else []
        if "FROM deltallm_organizationtable" in sql and "WHERE organization_id = $1" in sql:
            return [{"organization_id": params[0]}] if self.organization_exists else []
        if "SELECT COUNT(*)::int AS overlap_count" in sql:
            return [{"overlap_count": self.overlap_count}]
        if (
            "SELECT COUNT(*)::int AS count" in sql
            and "FROM deltallm_organizationtierassignment" in sql
        ):
            if "enabled = TRUE" in sql:
                return [{"count": self.active_assignment_count}]
            return [{"count": len(self.assignment_rows)}]
        if "tier_version_id IS NULL" in sql:
            return [{"assignment_count": self.unpinned_assignment_count}]
        if "WHERE tier_version_id = $1" in sql and "enabled = TRUE" in sql:
            return [{"assignment_count": self.pinned_assignment_count}]
        if (
            "SELECT v.tier_version_id" in sql
            and "v.status = 'active'" in sql
            and "v.tier_version_id <> $2" in sql
        ):
            if self.current_active_version_id is None:
                return []
            return [{"tier_version_id": self.current_active_version_id}]
        if (
            "SELECT v.tier_version_id" in sql
            and "FROM deltallm_tierversion v" in sql
            and "WHERE v.tier_id = $1" in sql
            and "v.status = 'active'" in sql
            and "FOR SHARE OF v" in sql
        ):
            if self.current_active_version_id is None:
                return []
            return [{"tier_version_id": self.current_active_version_id}]
        if "SELECT COUNT(*)::int AS total FROM deltallm_tier" in sql:
            return [{"total": 1}]
        if "FROM deltallm_tier t" in sql and "ORDER BY t.created_at" in sql:
            return [_tier_row()]
        if "INSERT INTO deltallm_tier (" in sql:
            return [
                _tier_row(
                    tier_key=str(params[0]),
                    name=str(params[1]),
                    description=str(params[2]) if params[2] is not None else None,
                    enabled=bool(params[3]),
                    metadata=params[4],
                    active_version_id=None,
                    version_count=0,
                    assignment_count=0,
                )
            ]
        if "INSERT INTO deltallm_tierversion" in sql:
            return [
                _version_row(
                    tier_id=str(params[0]),
                    version_number=int(params[1]),
                    status=str(params[2]),
                    published_at=params[3],
                    published_by_account_id=str(params[4]) if params[4] is not None else None,
                    metadata=params[5],
                )
            ]
        if "UPDATE deltallm_tierversion AS v" in sql and "SET status = 'active'" in sql:
            return [
                _version_row(
                    status="active",
                    published_by_account_id=str(params[1]) if params[1] is not None else None,
                )
            ]
        if "UPDATE deltallm_tierversion AS v" in sql and "SET status = 'archived'" in sql:
            return [_version_row(status="archived")]
        if "FROM deltallm_tierversion v" in sql and "WHERE v.tier_version_id = $1" in sql:
            return [
                _version_row(tier_id="tier-1", version_number=2, status=self.version_lookup_status)
            ]
        if "FROM deltallm_tierversion v" in sql and "WHERE v.tier_id = $1" in sql:
            return [
                _version_row(
                    tier_id=str(params[0]),
                    version_number=2,
                    status="active" if "v.status = 'active'" in sql else "draft",
                )
            ]
        if "INSERT INTO deltallm_tiermodelpolicy" in sql:
            return [
                _model_policy_row(
                    tier_version_id=str(params[0]),
                    callable_key=str(params[1]),
                    enabled=bool(params[2]),
                    access_mode=str(params[3]),
                    rpm_limit=params[4],
                    tpm_limit=params[5],
                    pricing=params[12],
                    capacity_pool_key=str(params[13]) if params[13] is not None else None,
                    priority=int(params[14]),
                    metadata=params[15],
                )
            ]
        if "INSERT INTO deltallm_tiercapacitypool" in sql:
            return [
                _capacity_pool_row(
                    tier_version_id=str(params[0]),
                    pool_key=str(params[1]),
                    callable_key=str(params[2]),
                    rpm_capacity=params[3],
                    tpm_capacity=params[4],
                    max_parallel_requests=params[5],
                    strategy=str(params[6]),
                    saturation_threshold=params[7],
                    burst_multiplier=params[8],
                    metadata=params[9],
                )
            ]
        if "INSERT INTO deltallm_organizationtierassignment" in sql:
            assignment_id = "assign-1"
            self.assignment_rows[assignment_id] = _assignment_fields(
                organization_id=str(params[0]),
                tier_id=str(params[1]),
                tier_version_id=str(params[2]) if params[2] is not None else None,
                assignment_type=str(params[3]),
                enabled=bool(params[4]),
                weight=int(params[5]),
                starts_at=params[6],
                ends_at=params[7],
                metadata=params[8],
            )
            return [{"assignment_id": assignment_id}]
        if "UPDATE deltallm_organizationtierassignment" in sql:
            assignment_id = str(params[0])
            organization_id = str(params[1])
            existing = self.assignment_rows.get(assignment_id)
            if "AND organization_id = $2" in sql:
                if existing is None:
                    return []
                if str(existing.get("organization_id") or "") != organization_id:
                    return []
            self.assignment_rows[assignment_id] = _assignment_fields(
                organization_id=organization_id,
                tier_id=str(params[2]),
                tier_version_id=str(params[3]) if params[3] is not None else None,
                assignment_type=str(params[4]),
                enabled=bool(params[5]),
                weight=int(params[6]),
                starts_at=params[7],
                ends_at=params[8],
                metadata=params[9],
            )
            return [{"assignment_id": assignment_id}]
        if "DELETE FROM deltallm_organizationtierassignment" in sql:
            assignment_id = str(params[0])
            existing = self.assignment_rows.get(assignment_id)
            if existing is None:
                return []
            if "AND organization_id = $2" in sql:
                organization_id = str(params[1])
                if (
                    existing is not None
                    and str(existing.get("organization_id") or "") != organization_id
                ):
                    return []
            self.assignment_rows.pop(assignment_id, None)
            return [{"assignment_id": assignment_id}]
        if "FROM deltallm_organizationtierassignment a" in sql:
            assignment_id = str(params[0])
            fields = self.assignment_rows.get(assignment_id)
            if "FOR UPDATE OF a" in sql and fields is None:
                return []
            if "a.organization_id = $2" in sql:
                organization_id = str(params[1])
                if fields is not None and str(fields.get("organization_id") or "") != organization_id:
                    return []
            return [
                _assignment_row(
                    assignment_id=assignment_id,
                    fields=fields,
                )
            ]
        return []

    async def execute_raw(self, sql: str, *params: object) -> int:
        self.executions.append((sql, params))
        if self.fail_on_sql and self.fail_on_sql in sql:
            raise RuntimeError("simulated execute failure")
        return 1


class _FakeTxContext:
    def __init__(self, root: _FakePrisma) -> None:
        self.root = root
        self.client: _FakePrisma | None = None

    async def __aenter__(self) -> _FakePrisma:
        self.root.tx_started += 1
        self.client = _FakePrisma(
            overlap_count=self.root.overlap_count,
            fail_on_sql=self.root.fail_on_sql,
            assignment_version_tier_id=self.root.assignment_version_tier_id,
            assignment_version_status=self.root.assignment_version_status,
            assignment_tier_exists=self.root.assignment_tier_exists,
            mutation_version_status=self.root.mutation_version_status,
            version_lookup_status=self.root.version_lookup_status,
            unpinned_assignment_count=self.root.unpinned_assignment_count,
            pinned_assignment_count=self.root.pinned_assignment_count,
            active_assignment_count=self.root.active_assignment_count,
            current_active_version_id=self.root.current_active_version_id,
            assignment_rows=self.root.assignment_rows,
            organization_exists=self.root.organization_exists,
        )
        self.root.tx_clients.append(self.client)
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        del exc, tb
        if exc_type is None:
            self.root.tx_committed += 1
            if self.client is not None:
                self.root.assignment_rows = dict(self.client.assignment_rows)
        else:
            self.root.tx_rolled_back += 1
        return False


def _tier_row(
    *,
    tier_key: str = "pro",
    name: str = "Pro",
    description: str | None = "Scaled access",
    enabled: bool = True,
    metadata: object = '{"segment": "growth"}',
    active_version_id: str | None = "ver-active",
    version_count: int = 2,
    assignment_count: int = 3,
) -> dict[str, object]:
    return {
        "tier_id": "tier-1",
        "tier_key": tier_key,
        "name": name,
        "description": description,
        "enabled": enabled,
        "metadata": metadata,
        "active_version_id": active_version_id,
        "version_count": version_count,
        "assignment_count": assignment_count,
        "created_at": None,
        "updated_at": None,
    }


def _version_row(
    *,
    tier_id: str = "tier-1",
    version_number: int = 1,
    status: str = "draft",
    published_at: object = None,
    published_by_account_id: str | None = None,
    metadata: object = None,
) -> dict[str, object]:
    return {
        "tier_version_id": "ver-1",
        "tier_id": tier_id,
        "version_number": version_number,
        "status": status,
        "published_at": published_at,
        "published_by_account_id": published_by_account_id,
        "metadata": metadata,
        "model_policy_count": 0,
        "capacity_pool_count": 0,
        "assignment_count": 0,
        "created_at": None,
        "updated_at": None,
    }


def _model_policy_row(
    *,
    tier_version_id: str,
    callable_key: str,
    enabled: bool,
    access_mode: str,
    rpm_limit: object,
    tpm_limit: object,
    pricing: object,
    capacity_pool_key: str | None,
    priority: int,
    metadata: object,
) -> dict[str, object]:
    return {
        "tier_model_policy_id": "policy-1",
        "tier_version_id": tier_version_id,
        "callable_key": callable_key,
        "enabled": enabled,
        "access_mode": access_mode,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "rph_limit": None,
        "rpd_limit": None,
        "tpd_limit": None,
        "max_parallel_requests": None,
        "batch_rpm_limit": None,
        "batch_tpm_limit": None,
        "pricing": pricing,
        "capacity_pool_key": capacity_pool_key,
        "priority": priority,
        "metadata": metadata,
        "created_at": None,
        "updated_at": None,
    }


def _capacity_pool_row(
    *,
    tier_version_id: str,
    pool_key: str,
    callable_key: str,
    rpm_capacity: object,
    tpm_capacity: object,
    max_parallel_requests: object,
    strategy: str,
    saturation_threshold: object,
    burst_multiplier: object,
    metadata: object,
) -> dict[str, object]:
    return {
        "tier_capacity_pool_id": "pool-1",
        "tier_version_id": tier_version_id,
        "pool_key": pool_key,
        "callable_key": callable_key,
        "rpm_capacity": rpm_capacity,
        "tpm_capacity": tpm_capacity,
        "max_parallel_requests": max_parallel_requests,
        "strategy": strategy,
        "saturation_threshold": saturation_threshold,
        "burst_multiplier": burst_multiplier,
        "metadata": metadata,
        "created_at": None,
        "updated_at": None,
    }


def _assignment_fields(
    *,
    organization_id: str,
    tier_id: str,
    tier_version_id: str | None,
    assignment_type: str,
    enabled: bool,
    weight: int,
    starts_at: object,
    ends_at: object,
    metadata: object,
) -> dict[str, object]:
    return {
        "organization_id": organization_id,
        "tier_id": tier_id,
        "tier_version_id": tier_version_id,
        "assignment_type": assignment_type,
        "enabled": enabled,
        "weight": weight,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "metadata": metadata,
    }


def _assignment_row(
    *,
    assignment_id: str,
    fields: dict[str, object] | None = None,
) -> dict[str, object]:
    row = {
        "assignment_id": assignment_id,
        "organization_id": "org-1",
        "tier_id": "tier-1",
        "tier_version_id": "ver-1",
        "assignment_type": "primary",
        "enabled": True,
        "weight": 1,
        "starts_at": None,
        "ends_at": None,
        "metadata": '{"reason": "signup"}',
        "tier_key": "pro",
        "tier_name": "Pro",
        "tier_version_number": 2,
        "tier_version_status": "active",
        "created_at": None,
        "updated_at": None,
    }
    if fields is not None:
        row.update(fields)
        if row["tier_version_id"] is None:
            row["tier_version_number"] = None
            row["tier_version_status"] = None
    return row
