"""Optional transaction pooler (PgBouncer) between the application and PostgreSQL.

Without a pooler, every pod pool opens its own PostgreSQL connections and sets
its server deadlines with the startup ``options`` parameter. A transaction
pooler does not pass ``options`` to the server, so each allocation connects to
its own pooler database (alias) instead. The alias sets that allocation's
deadlines with ``connect_query`` when the pooler opens a server connection, and
the application still checks at startup that they apply.

``DELTALLM_DATABASE_POOLER_URL`` is a URL template with ``{allocation}`` in
place of the per-allocation part, for example
``postgresql://deltallm:secret@pgbouncer:6432/deltallm_{allocation}``.
Migrations and the external-auth pool keep the direct ``DATABASE_URL``.
"""

from __future__ import annotations

from typing import Literal, get_args
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PoolerAllocation = Literal[
    "control",
    "foreground",
    "telemetry",
    "telemetry_worker",
    "telemetry_settlement",
    "accounting",
]
POOLER_ALLOCATIONS: tuple[str, ...] = get_args(PoolerAllocation)
POOLER_PLACEHOLDER = "{allocation}"


def validate_pooler_url(url: str | None) -> str | None:
    """Return the normalized pooler URL template, or None when no pooler is set."""
    if url is None or not url.strip():
        return None
    template = url.strip()
    if POOLER_PLACEHOLDER not in template:
        raise ValueError(
            "DELTALLM_DATABASE_POOLER_URL must contain {allocation}, for example "
            "postgresql://user:password@pgbouncer:6432/deltallm_{allocation}"
        )
    parsed = urlsplit(template.replace(POOLER_PLACEHOLDER, "allocation"))
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("DELTALLM_DATABASE_POOLER_URL must be a postgresql:// URL with a host")
    query = {name for name, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if "options" in query:
        raise ValueError(
            "DELTALLM_DATABASE_POOLER_URL must not set options: a transaction pooler "
            "does not pass them. Set deadlines with each alias's connect_query."
        )
    return template


def pooler_allocation_url(template: str, allocation: str) -> str:
    """Return the pooler URL of one allocation."""
    if allocation not in POOLER_ALLOCATIONS:
        raise ValueError(f"Unknown pooler allocation: {allocation}")
    return template.replace(POOLER_PLACEHOLDER, allocation)


def database_schema(url: str) -> str:
    """Return the PostgreSQL schema of a Prisma URL (``schema``, default ``public``)."""
    schema = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True)).get("schema", "public")
    if not schema or "\x00" in schema or len(schema) > 63:
        raise ValueError("PostgreSQL schema must be a valid bounded identifier")
    return schema


def quoted_schema(schema: str) -> str:
    """Return the schema as a quoted identifier for ``search_path``."""
    return '"' + schema.replace('"', '""') + '"'


def pooled_database_url(direct_url: str, template: str, allocation: str) -> str:
    """Return the pooler URL of one allocation with the schema of the direct URL.

    Prisma qualifies its queries with the URL schema. With ``pgbouncer=true`` it
    also sets ``search_path`` with a session ``SET`` on one server connection
    only. Each alias sets the same ``search_path`` with ``connect_query``, so
    every server connection uses the schema of ``DATABASE_URL``.
    """
    url = pooler_allocation_url(template, allocation)
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    schema = database_schema(direct_url)
    if "schema" in query and query["schema"] != schema:
        raise ValueError("DELTALLM_DATABASE_POOLER_URL must use the schema of DATABASE_URL")
    if "schema" in dict(parse_qsl(urlsplit(direct_url).query, keep_blank_values=True)):
        query["schema"] = schema
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def pgbouncer_database_lines(
    *,
    alias_template: str,
    host: str,
    port: int,
    dbname: str,
    settings: dict[str, tuple[tuple[str, str], ...]],
) -> list[str]:
    """Return PgBouncer ``[databases]`` lines: one alias per allocation.

    ``settings`` maps each allocation to the session settings that its alias
    sets with ``connect_query`` when PgBouncer opens a server connection.
    """
    if POOLER_PLACEHOLDER not in alias_template:
        raise ValueError("The alias template must contain {allocation}")
    for value in (host, dbname, alias_template):
        if not value or any(character in value for character in " '\"=\n"):
            raise ValueError("Host, database and alias must be plain names")
    lines = []
    for allocation in POOLER_ALLOCATIONS:
        statements = "; ".join(f"SET {name} = {value}" for name, value in settings[allocation])
        if "'" in statements:
            raise ValueError("Session settings must not contain single quotes")
        alias = pooler_allocation_url(alias_template, allocation)
        lines.append(
            f"{alias} = host={host} port={int(port)} dbname={dbname} connect_query='{statements}'"
        )
    return lines
