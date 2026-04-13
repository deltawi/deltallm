from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Protocol


@dataclass
class StagedBatchCreateArtifact:
    storage_backend: str
    storage_key: str
    bytes_size: int
    checksum: str | None = None


class BatchCreateStagingBackend(Protocol):
    async def write_lines(self, lines: Iterable[bytes] | AsyncIterator[bytes]) -> StagedBatchCreateArtifact:
        ...

    def read_lines(self, artifact: StagedBatchCreateArtifact) -> AsyncIterator[bytes]:
        ...

    async def delete(self, artifact: StagedBatchCreateArtifact) -> None:
        ...
