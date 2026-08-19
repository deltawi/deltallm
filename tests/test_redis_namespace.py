from src.redis_namespace import build_redis_channel


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
