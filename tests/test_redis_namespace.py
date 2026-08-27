from src.redis_namespace import build_redis_channel, build_redis_key
from src.router.redis_keys import RouterRedisKeyspace


def test_redis_channel_is_scoped_by_application_environment_and_schema() -> None:
    assert (
        build_redis_channel(
            application="Delta LLM / Saudi",
            environment="Production West",
            schema_version=2,
            capability="Audit Content Policy",
        )
        == "delta-llm-saudi:production-west:v2:audit-content-policy"
    )


def test_redis_key_scopes_and_escapes_identifiers() -> None:
    assert (
        build_redis_key(
            application="DeltaLLM",
            environment="staging",
            schema_version=1,
            capability="router health probe",
            identifiers=("deployment:a/b",),
        )
        == "deltallm:staging:v1:router-health-probe:deployment%3Aa%2Fb"
    )


def test_router_keyspace_namespaces_every_shared_state_capability() -> None:
    keyspace = RouterRedisKeyspace(environment="Production West")
    deployment_id = "deployment:a/b"

    assert keyspace.active_requests(deployment_id) == (
        "deltallm:production-west:v1:router-active-requests:deployment%3Aa%2Fb"
    )
    assert keyspace.attempt_owners(deployment_id) == (
        "deltallm:production-west:v1:router-attempt-owners:deployment%3Aa%2Fb"
    )
    assert keyspace.cooldown(deployment_id) == (
        "deltallm:production-west:v1:router-cooldown:deployment%3Aa%2Fb:legacy"
    )
    assert keyspace.health(deployment_id) == (
        "deltallm:production-west:v1:router-health:deployment%3Aa%2Fb:legacy"
    )
    assert keyspace.health_failures(deployment_id) == (
        "deltallm:production-west:v1:router-health-failures:deployment%3Aa%2Fb:legacy"
    )
    assert keyspace.latency(deployment_id) == (
        "deltallm:production-west:v1:router-latency:deployment%3Aa%2Fb"
    )
    assert keyspace.usage(deployment_id, "tpm", "2026-08-21T10:05") == (
        "deltallm:production-west:v1:router-usage:deployment%3Aa%2Fb:tpm:2026-08-21T10%3A05"
    )
    assert keyspace.health_recovery(deployment_id) == (
        "deltallm:production-west:v1:router-health-recovery:deployment%3Aa%2Fb:legacy"
    )
    assert keyspace.health_probe(deployment_id, "manual") == (
        "deltallm:production-west:v1:router-health-probe:manual:deployment%3Aa%2Fb:legacy"
    )
