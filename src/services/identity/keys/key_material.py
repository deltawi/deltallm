from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import secrets
from typing import Protocol

MINIMUM_KEY_LENGTH = 16
MAXIMUM_KEY_LENGTH = 8192


class KeyHasher(Protocol):
    def hash_key(self, raw_key: str) -> str: ...


class InvalidKeyMaterial(ValueError):
    pass


@dataclass(frozen=True)
class KeyMaterial:
    raw_key: str = field(repr=False)
    token_hash: str = field(repr=False)


def prepare_key_material(
    supplied: object, *, hasher: KeyHasher, master_key: str | None
) -> KeyMaterial:
    if supplied is None:
        raw_key = f"sk-{secrets.token_urlsafe(24)}"
    else:
        if not isinstance(supplied, str):
            raise InvalidKeyMaterial("key must be a string or null")
        if not MINIMUM_KEY_LENGTH <= len(supplied) <= MAXIMUM_KEY_LENGTH:
            raise InvalidKeyMaterial("key must contain between 16 and 8192 characters")
        if not supplied.startswith("sk-"):
            raise InvalidKeyMaterial("key must start with sk-")
        if any(not 33 <= ord(character) <= 126 for character in supplied):
            raise InvalidKeyMaterial("key must contain only ASCII characters without spaces")
        if master_key and hmac.compare_digest(supplied.encode(), master_key.encode()):
            raise InvalidKeyMaterial("key must differ from the master key")
        raw_key = supplied
    return KeyMaterial(raw_key=raw_key, token_hash=hasher.hash_key(raw_key))
