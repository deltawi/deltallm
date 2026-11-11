"""A transaction pooler gets one alias per allocation and no startup options."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from src.config import (
    AppConfig,
    Settings,
    resolve_database_settings,
    resolve_external_auth_database_settings,
    resolve_telemetry_database_settings,
)
from src.database_pooler import (
    POOLER_ALLOCATIONS,
    pgbouncer_database_lines,
    pooler_allocation_url,
    validate_pooler_url,
)
from src.db.runtime import accounting_pool
from src.db.runtime.accounting_pool import AccountingPostgresManager, accounting_postgres_dsn
from src.db.runtime.allocation_config import DatabasePolicy

pytestmark = pytest.mark.hermetic

POOLER = "postgresql://app:secret@pgbouncer:6432/deltallm_{allocation}?sslmode=require"


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_no_pooler_url_means_no_pooler(value):
    assert validate_pooler_url(value) is None


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("postgresql://app:secret@pgbouncer:6432/deltallm", "{allocation}"),
        ("mysql://app@pgbouncer/deltallm_{allocation}", "postgresql://"),
        ("postgresql:///deltallm_{allocation}", "postgresql://"),
        (
            "postgresql://app@pgbouncer/deltallm_{allocation}?options=-c%20jit%3Doff",
            "must not set options",
        ),
    ],
)
def test_invalid_pooler_urls_are_refused(url, message):
    with pytest.raises(ValueError, match=message):
        validate_pooler_url(url)


def test_settings_validate_the_pooler_url_without_echoing_it(monkeypatch):
    monkeypatch.setenv("DELTALLM_DATABASE_POOLER_URL", "postgresql://app:hunter2@pgbouncer/db")
    with pytest.raises(ValueError) as error:
        Settings()
    assert "hunter2" not in str(error.value)
    monkeypatch.setenv("DELTALLM_DATABASE_POOLER_URL", POOLER)
    assert Settings().database_pooler_url == POOLER


def test_unknown_allocation_has_no_alias():
    with pytest.raises(ValueError):
        pooler_allocation_url(POOLER, "reporting")


@pytest.mark.parametrize("allocation", ["control", "foreground", "telemetry_settlement"])
def test_pooled_allocation_url_uses_its_alias_and_no_startup_options(allocation):
    policy = DatabasePolicy(allocation, 4, 0.2, 1.0, 0.2, 2.0)
    url = policy.connection_url("postgresql://direct/deltallm", pooler_url=POOLER)
    parsed = urlsplit(url)
    query = _query(url)
    assert parsed.hostname == "pgbouncer"
    assert parsed.port == 6432
    assert parsed.path == f"/deltallm_{allocation}"
    assert "options" not in query
    assert query["pgbouncer"] == ["true"]
    assert query["sslmode"] == ["require"]
    assert query["connection_limit"] == ["4"]
    assert query["application_name"] == [f"deltallm_{allocation}"]


def test_direct_allocation_url_keeps_startup_deadlines():
    policy = DatabasePolicy("foreground", 8, 0.2, 1.0, 0.2, 2.0)
    query = _query(policy.connection_url("postgresql://direct/deltallm"))
    assert "pgbouncer" not in query
    assert query["options"] == [
        "-c statement_timeout=1000 -c lock_timeout=200 -c idle_in_transaction_session_timeout=2000"
    ]
    assert policy.server_deadlines() == (
        ("statement_timeout", 1000),
        ("lock_timeout", 200),
        ("idle_in_transaction_session_timeout", 2000),
    )


def test_pool_settings_carry_the_pooler_but_external_auth_and_migrations_do_not():
    settings = Settings(database_url="postgresql://direct/deltallm", database_pooler_url=POOLER)
    config = AppConfig()
    primary = resolve_database_settings(config, settings)
    telemetry = resolve_telemetry_database_settings(config, settings)
    external = resolve_external_auth_database_settings(config, settings)
    assert primary is not None and telemetry is not None and external is not None
    assert primary.pooler_url == telemetry.pooler_url == POOLER
    # Migrations and the small external-auth pool keep the direct URL.
    assert urlsplit(primary.url).hostname == "direct"
    assert external.pooler_url is None


def test_accounting_dsn_drops_the_prisma_pooler_flag():
    dsn = accounting_postgres_dsn(
        "postgresql://app@pgbouncer:6432/deltallm_accounting?pgbouncer=true&sslmode=require"
    )
    assert _query(dsn) == {"sslmode": ["require"]}


async def test_pooled_accounting_pool_sends_no_session_settings_or_named_statements(monkeypatch):
    calls = {}

    class Pool:
        def acquire(self, timeout):
            pool = self

            class Acquired:
                async def __aenter__(self):
                    return pool

                async def __aexit__(self, *_):
                    return False

            return Acquired()

        async def fetchrow(self, *_args, **_kwargs):
            return {"statement_seconds": 0.25, "lock_seconds": 0.2}

        async def close(self):
            return None

    async def create_pool(**kwargs):
        calls.update(kwargs)
        return Pool()

    monkeypatch.setattr(accounting_pool.asyncpg, "create_pool", create_pool)
    manager = AccountingPostgresManager()
    settings = Settings(database_url="postgresql://direct/deltallm", database_pooler_url=POOLER)
    database = resolve_database_settings(AppConfig(), settings)
    await manager.connect(
        database,
        pool_size=2,
        acquisition_seconds=0.2,
        statement_seconds=0.25,
        lock_seconds=0.2,
    )
    try:
        assert urlsplit(calls["dsn"]).path == "/deltallm_accounting"
        assert calls["server_settings"] == {"application_name": "deltallm_accounting_hot_path"}
        assert calls["statement_cache_size"] == 0
        # No RESET ALL on release: it would clear the alias's session settings.
        assert calls["reset"] is not None
    finally:
        await manager.disconnect()


def test_pgbouncer_lines_set_each_alias_deadlines():
    settings = {
        name: (("statement_timeout", str(index + 1)), ("lock_timeout", "1"))
        for index, name in enumerate(POOLER_ALLOCATIONS)
    }
    lines = pgbouncer_database_lines(
        alias_template="gw_{allocation}",
        host="postgres.internal",
        port=5432,
        dbname="deltallm",
        settings=settings,
    )
    assert lines[0] == (
        "gw_control = host=postgres.internal port=5432 dbname=deltallm "
        "connect_query='SET statement_timeout = 1; SET lock_timeout = 1'"
    )
    assert [line.split(" ", 1)[0] for line in lines] == [
        f"gw_{name}" for name in POOLER_ALLOCATIONS
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"alias_template": "gw"},
        {"host": "db host"},
        {"dbname": "x'y"},
    ],
)
def test_pgbouncer_lines_refuse_unsafe_names(change):
    arguments = {
        "alias_template": "gw_{allocation}",
        "host": "postgres",
        "port": 5432,
        "dbname": "deltallm",
        "settings": {name: (("jit", "off"),) for name in POOLER_ALLOCATIONS},
    }
    arguments.update(change)
    with pytest.raises(ValueError):
        pgbouncer_database_lines(**arguments)


def test_generator_prints_the_aliases_for_the_running_configuration(tmp_path, monkeypatch, capsys):
    from src.config import get_settings
    from src.db import pooler_config

    config = tmp_path / "config.yaml"
    config.write_text(
        "general_settings:\n"
        "  db_statement_timeout_seconds: 1.5\n"
        "  accounting_statement_timeout_ms: 300\n"
    )
    monkeypatch.setenv("DELTALLM_CONFIG_PATH", str(config))
    monkeypatch.setenv("DELTALLM_DATABASE_POOLER_URL", POOLER.replace("deltallm_", "gw_"))
    get_settings.cache_clear()
    try:
        assert pooler_config.main(["--host", "postgres", "--dbname", "deltallm"]) == 0
    finally:
        get_settings.cache_clear()
    lines = capsys.readouterr().out.splitlines()
    aliases = {line.split(" = ", 1)[0]: line for line in lines if " = host=" in line}
    assert set(aliases) == {f"gw_{name}" for name in POOLER_ALLOCATIONS}
    assert "SET statement_timeout = 1500;" in aliases["gw_foreground"]
    assert "SET statement_timeout = 5000;" in aliases["gw_control"]
    assert "SET statement_timeout = 300;" in aliases["gw_accounting"]
    assert "SET jit = off" in aliases["gw_accounting"]
