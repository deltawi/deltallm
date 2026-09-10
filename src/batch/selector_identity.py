from uuid import UUID, NAMESPACE_URL, uuid5


def batch_selector_operation_id(batch_id: str, item_id: str) -> UUID:
    """Stable across claims, answer retries, outbox delivery and process death."""
    return uuid5(NAMESPACE_URL, f"deltallm:batch-selector:v1:{batch_id}:{item_id}")
