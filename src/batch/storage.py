from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable
from uuid import uuid4


class BatchArtifactStorage:
    async def write_bytes(self, *, purpose: str, filename: str, content: bytes) -> tuple[str, int, str]:
        raise NotImplementedError

    async def read_bytes(self, storage_key: str) -> bytes:
        raise NotImplementedError

    async def delete(self, storage_key: str) -> None:
        raise NotImplementedError

    async def write_lines(self, *, purpose: str, filename: str, lines: Iterable[str]) -> tuple[str, int, str]:
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        return await self.write_bytes(purpose=purpose, filename=filename, content=payload)


class LocalBatchArtifactStorage(BatchArtifactStorage):
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def write_bytes(self, *, purpose: str, filename: str, content: bytes) -> tuple[str, int, str]:
        safe_name = filename.replace("/", "_")
        storage_key = f"{purpose}/{uuid4().hex}-{safe_name}"
        target = self.base_dir / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        return storage_key, len(content), checksum

    async def read_bytes(self, storage_key: str) -> bytes:
        target = self.base_dir / storage_key
        return target.read_bytes()

    async def delete(self, storage_key: str) -> None:
        target = self.base_dir / storage_key
        if target.exists():
            target.unlink()
