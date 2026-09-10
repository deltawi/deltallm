"""Local-only Docker acceptance. Never use these fixture credentials outside loopback."""

import argparse
import asyncio
import hashlib
import json
import os
from urllib.parse import urlsplit

import httpx
from prisma import Prisma

KEY = "sk-independent-smoke-caller-fixture"
SALT = "independent-local-test-salt-20260910"
BASE = "http://127.0.0.1:4003"


async def seed():
    url = os.environ["DATABASE_URL"]
    parsed = urlsplit(url)
    if parsed.hostname != "127.0.0.1" or parsed.path != "/independent_selector_smoke":
        raise ValueError("This fixture requires the disposable loopback smoke database")
    db = Prisma(datasource={"url": url})
    await db.connect()
    token = hashlib.sha256(f"{SALT}:{KEY}".encode()).hexdigest()
    try:
        async with db.tx() as tx:
            await tx.execute_raw(
                """
                INSERT INTO deltallm_verificationtoken(id, token, key_name, models, expires)
                VALUES ('independent-smoke-key', $1, 'Local smoke fixture', ARRAY[]::text[], NOW()+INTERVAL '1 hour')
                ON CONFLICT (token) DO UPDATE SET expires=EXCLUDED.expires
                """,
                token,
            )
            await tx.execute_raw(
                """
                INSERT INTO deltallm_callabletargetbinding(callable_target_binding_id, callable_key, scope_type, scope_id, updated_at)
                VALUES ('independent-smoke-binding', 'independent-answers', 'api_key', $1, NOW())
                ON CONFLICT (callable_key,scope_type,scope_id) DO NOTHING
                """,
                token,
            )
        print("Seeded one expiring fixture key and group-only grant; restart the isolated gateway.")
    finally:
        await db.disconnect()


async def check():
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as client:
        assert (await client.get("/health/liveliness")).status_code == 200
        readiness = await client.get("/health/readiness")
        assert readiness.status_code == 200, readiness.text
        headers = {"Authorization": f"Bearer {KEY}"}
        for endpoint, streaming, prompt in [
            ("/v1/chat/completions", False, "Routine greeting"),
            ("/v1/chat/completions", False, "complex task: reasoning"),
            ("/v1/chat/completions", True, "Routine streaming"),
            ("/v1/responses", False, "complex task: responses"),
        ]:
            body = {"model": "independent-answers", "stream": streaming}
            body.update(
                {"input": prompt}
                if endpoint.endswith("responses")
                else {"messages": [{"role": "user", "content": prompt}], "max_tokens": 8}
            )
            response = await client.post(endpoint, json=body, headers=headers)
            assert response.status_code == 200, response.text
            if not streaming:
                assert response.json()["usage"]["total_tokens"] == 18
            print(
                json.dumps(
                    {"endpoint": endpoint, "stream": streaming, "status": response.status_code}
                )
            )
        denied = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "tiny-selector", "messages": [{"role": "user", "content": "deny"}]},
        )
        assert denied.status_code == 403, denied.text
        print("Direct selector access denied; answer-only public usage preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    asyncio.run(seed() if parser.parse_args().seed else check())
