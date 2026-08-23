from __future__ import annotations


# All statements embedding these fragments bind the target organization as $1.
# Historical sensitive records are attributed only through persisted organization,
# team, or API-key claims. Current user membership is a blocker hint, never proof
# that a historical record belongs to the user's current organization.
ORGANIZATION_SCOPE_INVENTORY_CTE_SQL = """
team_organizations AS MATERIALIZED (
    SELECT t.team_id, t.organization_id
    FROM deltallm_teamtable t
    UNION ALL
    SELECT tombstone.team_id, tombstone.organization_id
    FROM deltallm_teamtombstone tombstone
    WHERE NOT EXISTS (
        SELECT 1 FROM deltallm_teamtable live WHERE live.team_id = tombstone.team_id
    )
),
key_organizations AS MATERIALIZED (
    SELECT v.token, team.organization_id
    FROM deltallm_verificationtoken v
    LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
    LEFT JOIN deltallm_serviceaccount s
      ON s.service_account_id = v.owner_service_account_id
    LEFT JOIN team_organizations team
      ON team.team_id = COALESCE(v.team_id, u.team_id, s.team_id)
),
target_teams AS MATERIALIZED (
    SELECT team_id
    FROM team_organizations
    WHERE organization_id = $1
),
target_keys AS MATERIALIZED (
    SELECT token
    FROM key_organizations
    WHERE organization_id = $1
),
target_candidate_users AS MATERIALIZED (
    SELECT u.user_id
    FROM deltallm_usertable u
    JOIN target_teams target ON target.team_id = u.team_id
    UNION
    SELECT v.user_id
    FROM deltallm_verificationtoken v
    JOIN target_keys target ON target.token = v.token
    WHERE v.user_id IS NOT NULL
    UNION
    SELECT m.account_id
    FROM deltallm_organizationmembership m
    WHERE m.organization_id = $1
    UNION
    SELECT tombstone.principal_id
    FROM deltallm_organizationprincipaltombstone tombstone
    WHERE tombstone.organization_id = $1
)
"""


ORGANIZATION_SCOPE_PREDICATE_SQL = """
(
    {alias}.scope_type = 'organization' AND {alias}.scope_id = $1
) OR (
    {alias}.scope_type = 'team'
    AND {alias}.scope_id IN (SELECT team_id FROM target_teams)
) OR (
    {alias}.scope_type = 'api_key'
    AND {alias}.scope_id IN (SELECT token FROM target_keys)
)
"""


PROMPT_LOG_CONFLICT_PREDICATE_SQL = """
(
    {alias}.organization_id IS NOT NULL
    AND (
        EXISTS (
            SELECT 1 FROM team_organizations team
            WHERE team.team_id = {alias}.team_id
              AND team.organization_id IS NOT NULL
              AND team.organization_id <> {alias}.organization_id
        )
        OR EXISTS (
            SELECT 1 FROM key_organizations key_owner
            WHERE key_owner.token = {alias}.api_key
              AND key_owner.organization_id IS NOT NULL
              AND key_owner.organization_id <> {alias}.organization_id
        )
    )
) OR (
    {alias}.organization_id IS NULL
    AND {alias}.team_id IS NOT NULL
    AND {alias}.api_key IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM team_organizations team
        JOIN key_organizations key_owner ON key_owner.token = {alias}.api_key
        WHERE team.team_id = {alias}.team_id
          AND team.organization_id IS NOT NULL
          AND key_owner.organization_id IS NOT NULL
          AND team.organization_id <> key_owner.organization_id
    )
)
"""


PROMPT_LOG_TARGET_CLAIM_SQL = """
{alias}.organization_id = $1
OR {alias}.team_id IN (SELECT team_id FROM target_teams)
OR {alias}.api_key IN (SELECT token FROM target_keys)
"""


PROMPT_LOG_ATTRIBUTION_PREDICATE_SQL = """
NOT ({conflict_predicate})
AND (
    {alias}.organization_id = $1
    OR (
        {alias}.organization_id IS NULL
        AND {alias}.team_id IN (SELECT team_id FROM target_teams)
    )
    OR (
        {alias}.organization_id IS NULL
        AND {alias}.team_id IS NULL
        AND {alias}.api_key IN (SELECT token FROM target_keys)
    )
)
"""


CONFLICTING_PROMPT_LOG_PREDICATE_SQL = """
({conflict_predicate})
AND ({target_claim})
"""


UNATTRIBUTED_PROMPT_LOG_PREDICATE_SQL = """
{alias}.organization_id IS NULL
AND (
    {alias}.team_id IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM team_organizations team WHERE team.team_id = {alias}.team_id
    )
)
AND (
    {alias}.api_key IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM key_organizations key_owner
        WHERE key_owner.token = {alias}.api_key
    )
)
AND {alias}.user_id IN (SELECT user_id FROM target_candidate_users)
"""


APPROVAL_SCOPE_CONFLICT_WITH_ORGANIZATION_SQL = """
(
    {alias}.scope_type = 'organization'
    AND {alias}.scope_id <> {alias}.organization_id
) OR EXISTS (
    SELECT 1 FROM team_organizations team
    WHERE {alias}.scope_type = 'team'
      AND team.team_id = {alias}.scope_id
      AND team.organization_id IS NOT NULL
      AND team.organization_id <> {alias}.organization_id
) OR EXISTS (
    SELECT 1 FROM key_organizations key_owner
    WHERE {alias}.scope_type = 'api_key'
      AND key_owner.token = {alias}.scope_id
      AND key_owner.organization_id IS NOT NULL
      AND key_owner.organization_id <> {alias}.organization_id
)
"""


APPROVAL_SCOPE_KEY_CONFLICT_SQL = """
{alias}.organization_id IS NULL
AND {alias}.requested_by_api_key IS NOT NULL
AND EXISTS (
    SELECT 1
    FROM key_organizations requester
    WHERE requester.token = {alias}.requested_by_api_key
      AND requester.organization_id IS NOT NULL
      AND (
          (
              {alias}.scope_type = 'organization'
              AND requester.organization_id <> {alias}.scope_id
          )
          OR EXISTS (
              SELECT 1 FROM team_organizations team
              WHERE {alias}.scope_type = 'team'
                AND team.team_id = {alias}.scope_id
                AND team.organization_id IS NOT NULL
                AND team.organization_id <> requester.organization_id
          )
          OR EXISTS (
              SELECT 1 FROM key_organizations scoped_key
              WHERE {alias}.scope_type = 'api_key'
                AND scoped_key.token = {alias}.scope_id
                AND scoped_key.organization_id IS NOT NULL
                AND scoped_key.organization_id <> requester.organization_id
          )
      )
)
"""


APPROVAL_CONFLICT_PREDICATE_SQL = """
(
    {alias}.organization_id IS NOT NULL
    AND (
        {scope_org_conflict}
        OR EXISTS (
            SELECT 1 FROM key_organizations requester
            WHERE requester.token = {alias}.requested_by_api_key
              AND requester.organization_id IS NOT NULL
              AND requester.organization_id <> {alias}.organization_id
        )
    )
) OR ({scope_key_conflict})
"""


APPROVAL_TARGET_CLAIM_SQL = """
{alias}.organization_id = $1
OR ({scope_predicate})
OR {alias}.requested_by_api_key IN (SELECT token FROM target_keys)
"""


APPROVAL_ATTRIBUTION_PREDICATE_SQL = """
NOT ({conflict_predicate})
AND (
    {alias}.organization_id = $1
    OR (
        {alias}.organization_id IS NULL
        AND ({scope_predicate})
    )
)
"""


CONFLICTING_APPROVAL_PREDICATE_SQL = """
({conflict_predicate})
AND ({target_claim})
"""


UNATTRIBUTED_APPROVAL_PREDICATE_SQL = """
{alias}.organization_id IS NULL
AND (
    (
        {alias}.scope_type = 'user'
        AND {alias}.scope_id IN (SELECT user_id FROM target_candidate_users)
    )
    OR (
        {alias}.requested_by_user IN (SELECT user_id FROM target_candidate_users)
        AND (
            {alias}.scope_type NOT IN ('organization', 'team', 'api_key')
            OR (
                {alias}.scope_type = 'team'
                AND NOT EXISTS (
                    SELECT 1 FROM team_organizations team
                    WHERE team.team_id = {alias}.scope_id
                )
            )
            OR (
                {alias}.scope_type = 'api_key'
                AND NOT EXISTS (
                    SELECT 1 FROM key_organizations key_owner
                    WHERE key_owner.token = {alias}.scope_id
                )
            )
        )
    )
)
"""


def scope_predicate(alias: str) -> str:
    if alias not in {"a", "b", "p", "scoped", "target"}:
        raise ValueError("unsupported organization scope SQL alias")
    return ORGANIZATION_SCOPE_PREDICATE_SQL.format(alias=alias)


def prompt_log_conflict_predicate(alias: str = "l") -> str:
    if alias != "l":
        raise ValueError("unsupported prompt log SQL alias")
    return PROMPT_LOG_CONFLICT_PREDICATE_SQL.format(alias=alias)


def prompt_log_attribution_predicate(alias: str = "l") -> str:
    if alias != "l":
        raise ValueError("unsupported prompt log SQL alias")
    return PROMPT_LOG_ATTRIBUTION_PREDICATE_SQL.format(
        alias=alias,
        conflict_predicate=prompt_log_conflict_predicate(alias),
    )


def conflicting_prompt_log_predicate(alias: str = "l") -> str:
    return CONFLICTING_PROMPT_LOG_PREDICATE_SQL.format(
        conflict_predicate=prompt_log_conflict_predicate(alias),
        target_claim=PROMPT_LOG_TARGET_CLAIM_SQL.format(alias=alias),
    )


def unattributed_prompt_log_predicate(alias: str = "l") -> str:
    if alias != "l":
        raise ValueError("unsupported prompt log SQL alias")
    return UNATTRIBUTED_PROMPT_LOG_PREDICATE_SQL.format(alias=alias)


def approval_conflict_predicate(alias: str = "a") -> str:
    if alias != "a":
        raise ValueError("unsupported approval SQL alias")
    return APPROVAL_CONFLICT_PREDICATE_SQL.format(
        alias=alias,
        scope_org_conflict=APPROVAL_SCOPE_CONFLICT_WITH_ORGANIZATION_SQL.format(alias=alias),
        scope_key_conflict=APPROVAL_SCOPE_KEY_CONFLICT_SQL.format(alias=alias),
    )


def approval_attribution_predicate(alias: str = "a") -> str:
    if alias != "a":
        raise ValueError("unsupported approval SQL alias")
    return APPROVAL_ATTRIBUTION_PREDICATE_SQL.format(
        alias=alias,
        conflict_predicate=approval_conflict_predicate(alias),
        scope_predicate=scope_predicate(alias),
    )


def conflicting_approval_predicate(alias: str = "a") -> str:
    return CONFLICTING_APPROVAL_PREDICATE_SQL.format(
        conflict_predicate=approval_conflict_predicate(alias),
        target_claim=APPROVAL_TARGET_CLAIM_SQL.format(
            alias=alias,
            scope_predicate=scope_predicate(alias),
        ),
    )


def unattributed_approval_predicate(alias: str = "a") -> str:
    if alias != "a":
        raise ValueError("unsupported approval SQL alias")
    return UNATTRIBUTED_APPROVAL_PREDICATE_SQL.format(alias=alias)


def ambiguous_prompt_log_predicate(alias: str = "l") -> str:
    return (
        f"({conflicting_prompt_log_predicate(alias)}) "
        f"OR ({unattributed_prompt_log_predicate(alias)})"
    )


def ambiguous_approval_predicate(alias: str = "a") -> str:
    return (
        f"({conflicting_approval_predicate(alias)}) OR ({unattributed_approval_predicate(alias)})"
    )


__all__ = [
    "ORGANIZATION_SCOPE_INVENTORY_CTE_SQL",
    "ambiguous_approval_predicate",
    "ambiguous_prompt_log_predicate",
    "approval_attribution_predicate",
    "conflicting_approval_predicate",
    "conflicting_prompt_log_predicate",
    "prompt_log_attribution_predicate",
    "scope_predicate",
    "unattributed_approval_predicate",
    "unattributed_prompt_log_predicate",
]
