from collections.abc import Collection


def required_request_tags(metadata: object) -> tuple[str, ...]:
    tags = metadata.get("tags") if isinstance(metadata, dict) else None
    return (
        tuple(tag.strip() for tag in tags if isinstance(tag, str) and tag.strip())
        if isinstance(tags, list)
        else ()
    )


def tags_allow_deployment(tags: Collection[str], required: tuple[str, ...]) -> bool:
    return not required or bool(tags and all(tag in tags for tag in required))
