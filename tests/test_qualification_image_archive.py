"""A platform-only import retains a checked immutable image identity."""

import hashlib
from io import BytesIO
import json
import tarfile

import pytest

from tests.performance.qualification_image_archive import platform_manifest_digest


@pytest.mark.parametrize("change", ["valid", "wrong_digest", "multiple_platforms", "not_manifest"])
def test_platform_manifest_identity_is_verified(tmp_path, change):
    payload = json.dumps({"config": {}, "layers": []} if change != "not_manifest" else {}).encode()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    descriptors = [{"digest": digest}]
    if change == "multiple_platforms":
        descriptors *= 2
    if change == "wrong_digest":
        payload = b"{}"
    archive = tmp_path / "image.tar"
    with tarfile.open(archive, "w") as contents:
        for name, body in (
            ("index.json", json.dumps({"manifests": descriptors}).encode()),
            ("blobs/sha256/" + digest.removeprefix("sha256:"), payload),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(body)
            contents.addfile(member, BytesIO(body))
    if change == "valid":
        assert platform_manifest_digest(archive) == digest
    else:
        with pytest.raises(ValueError):
            platform_manifest_digest(archive)
