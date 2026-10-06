"""Keep the immutable platform identity from an owned Docker image archive."""

import hashlib
import json
from pathlib import Path
import re
import tarfile


def platform_manifest_digest(archive: Path) -> str:
    with tarfile.open(archive) as contents:
        index_member = contents.getmember("index.json")
        if not index_member.isfile() or index_member.size > 65536:
            raise ValueError("Fixture image index exceeds its byte limit")
        index_file = contents.extractfile(index_member)
        if index_file is None:
            raise ValueError("Fixture image index is missing")
        index = json.loads(index_file.read(65537))
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("Fixture archive must contain exactly one platform manifest")
        digest = manifests[0].get("digest")
        if not isinstance(digest, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is None:
            raise ValueError("Fixture platform digest is invalid")
        member = contents.getmember("blobs/sha256/" + digest.removeprefix("sha256:"))
        if not member.isfile() or member.size > 65536:
            raise ValueError("Fixture platform manifest exceeds its byte limit")
        manifest_file = contents.extractfile(member)
        if manifest_file is None:
            raise ValueError("Fixture platform manifest is missing")
        payload = manifest_file.read(65537)
        if hashlib.sha256(payload).hexdigest() != digest.removeprefix("sha256:"):
            raise ValueError("Fixture platform manifest does not match its digest")
        manifest = json.loads(payload)
        if "config" not in manifest or "layers" not in manifest:
            raise ValueError("Fixture descriptor is not a platform image manifest")
        return digest
