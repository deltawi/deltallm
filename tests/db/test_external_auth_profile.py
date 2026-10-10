from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

import pytest

from src.db.identity.external.external_auth_cleanup import ExternalAuthCleanupRepository
from src.db.identity.external.external_auth_sessions import ExternalSessionRepository
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_auth_exchange import enable, proof, services

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("DELTALLM_EXTERNAL_AUTH_PROFILE") != "1",
        reason="Opt-in representative-cardinality profile",
    ),
]
external_database = fixtures.external_database


class Capture:
    def __init__(self, db):
        self.db = db
        self.read = None

    async def query_raw(self, query, *params):
        self.read = (query, params)
        return await self.db.query_raw(query, *params)


def indexes(plan):
    result = set()
    if isinstance(plan, dict):
        if "Index Name" in plan:
            result.add(plan["Index Name"])
        for value in plan.values():
            result.update(indexes(value))
    elif isinstance(plan, list):
        for value in plan:
            result.update(indexes(value))
    return result


async def test_cardinality_validation_and_cleanup_profile(external_database):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "cardinality")
    token_hash = exchange.identities.sessions.hash_token(response.session_token)
    prefix = uuid4().hex
    try:
        await fixture.db.execute_raw(
            """INSERT INTO deltallm_externalauthparentsession
            (parent_id, integration_id, external_session_id_hash, subject_id, auth_time, expires_at, generation, updated_at)
            SELECT $2 || seq::text, $1, encode(digest($2 || seq::text, 'sha256'), 'hex'), $3,
                NOW(), NOW() + INTERVAL '1 hour', 1, NOW() FROM generate_series(1, 30000) seq""",
            fixture.integration_id,
            prefix,
            fixture.subject_id,
        )
        await fixture.db.execute_raw(
            """INSERT INTO deltallm_platformsession (session_id, account_id, session_token_hash, expires_at, updated_at,
                external_parent_id, external_generation, external_integration_epoch, external_binding_epoch, external_subject_epoch, revoked_at)
            SELECT $2 || seq::text, $1, $2 || seq::text,
                CASE WHEN seq <= 10000 THEN NOW() - INTERVAL '8 days' ELSE NOW() + INTERVAL '5 minutes' END,
                NOW(), $2 || seq::text, 1, 0, 0, 0, CASE WHEN seq <= 10000 THEN NOW() ELSE NULL END
            FROM generate_series(1, 30000) seq""",
            fixture.account_id,
            prefix,
        )
        await fixture.db.execute_raw(
            """INSERT INTO deltallm_externalauthassertionuse (integration_id, jti_hash, purpose, received_at, retain_until)
            SELECT $1, encode(digest($2 || seq::text, 'sha256'), 'hex'), 'gateway_session_exchange', NOW() - INTERVAL '16 minutes',
                CASE WHEN seq <= 10000 THEN NOW() - INTERVAL '1 minute' ELSE NOW() + INTERVAL '15 minutes' END
            FROM generate_series(1, 30000) seq""",
            fixture.integration_id,
            prefix,
        )
        await fixture.db.execute_raw("ANALYZE deltallm_platformsession")
        await fixture.db.execute_raw("ANALYZE deltallm_externalauthassertionuse")
        capture = Capture(fixture.db)
        assert await ExternalSessionRepository(capture).get_active(token_hash)
        query, params = capture.read
        plans = await fixture.db.query_raw(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, *params
        )
        plan = plans[0]["QUERY PLAN"]
        if isinstance(plan, str):
            plan = json.loads(plan)
        used_indexes = indexes(plan)
        assert any("session_token_hash" in name for name in used_indexes), used_indexes
        timings = []
        for _ in range(100):
            start = perf_counter()
            assert await sessions.get_context(token_hash)
            timings.append((perf_counter() - start) * 1000)
        cleanup_start = perf_counter()
        async with exchange.transactions.transaction("maintenance") as db:
            cleaned = await ExternalAuthCleanupRepository(db).clean(1000)
        cleanup_ms = (perf_counter() - cleanup_start) * 1000
        assert cleaned["assertions"] == 1000 and cleanup_ms < 750
        p95 = sorted(timings)[94]
        assert p95 < 50, p95
        output = os.getenv("DELTALLM_EXTERNAL_AUTH_PROFILE_OUTPUT")
        if output:
            Path(output).write_text(
                json.dumps(
                    {
                        "session_rows": 30000,
                        "assertion_rows": 30000,
                        "validation_queries": 2,
                        "validation_median_ms": median(timings),
                        "validation_p95_ms": p95,
                        "validation_p99_ms": sorted(timings)[98],
                        "cleanup_rows": cleaned,
                        "cleanup_ms": cleanup_ms,
                        "validation_indexes": sorted(used_indexes),
                    },
                    indent=2,
                )
            )
    finally:
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_platformsession WHERE account_id = $1 AND session_id LIKE $2",
            fixture.account_id,
            prefix + "%",
        )
