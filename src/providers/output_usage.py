"""Read bounded, raw provider output evidence before usage normalization."""

from collections.abc import Callable, Mapping

from src.services.output_limit_types import complete_output_count


def compatible_output_count(payload: object) -> int | None:
    if not isinstance(payload, Mapping) or "error" in payload:
        return None
    usage = payload.get("usage")
    metadata = payload.get("x_groq")
    groq_usage = metadata.get("usage") if isinstance(metadata, Mapping) else None
    if isinstance(metadata, Mapping) and metadata.get("error") is not None:
        return None
    count = complete_output_count(usage)
    groq_count = complete_output_count(groq_usage)
    if usage is not None and count is None or groq_usage is not None and groq_count is None:
        return None
    if count is not None and groq_count is not None and count != groq_count:
        return None
    return count if count is not None else groq_count


class OutputStreamEvidence:
    """Require final aggregate usage and completed choices; never sum cumulative counts."""

    def __init__(self, count: Callable[[object], int | None]) -> None:
        self.count = count
        self.seen: set[int] = set()
        self.finished: set[int] = set()
        self.final: int | None = None
        self.overflow = False

    def observe(self, payload: Mapping[str, object], choices: list[object]) -> None:
        for choice in choices:
            if isinstance(choice, Mapping):
                index = choice["index"]
                if len(self.seen) >= 128 and index not in self.seen:
                    self.overflow = True
                    return
                if index not in self.seen:
                    self.final = None
                self.seen.add(index)
                if choice.get("delta"):
                    self.final = None
                if not choice.get("finish_reason") and choice.get("delta"):
                    self.finished.discard(index)
                if choice.get("finish_reason"):
                    self.finished.add(index)
        # Metadata-only and terminal-choice frames both carry final aggregate usage.
        if self.seen and (not choices or self.seen == self.finished) and not self.overflow:
            metadata = payload.get("x_groq")
            has_usage = payload.get("usage") is not None or (
                isinstance(metadata, Mapping) and metadata.get("usage") is not None
            )
            if has_usage:
                self.final = self.count(payload)

    def complete(self, *, terminal: bool = False) -> int | None:
        return (
            self.final if (terminal or self.seen == self.finished) and not self.overflow else None
        )
