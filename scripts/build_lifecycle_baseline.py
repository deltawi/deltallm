"""Build the PR8 comparison baseline from committed source with identical packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.ref + "^{commit}"], text=True
    ).strip()
    with tempfile.TemporaryDirectory(prefix="deltallm-pr8-baseline-") as temporary:
        root = Path(temporary)
        archive = root / "source.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", "--output", str(archive), commit],
            check=True,
            timeout=60,
        )
        source = root / "source"
        source.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter="data")
        digest = hashlib.sha256()
        for path in sorted((source / "src").rglob("*.py")) + [source / "uv.lock"]:
            digest.update(str(path.relative_to(source)).encode() + b"\0" + path.read_bytes())
        # Compare application changes with the same interpreter/Node/image layout.
        # The baseline keeps its own dependency manifest and lock. Its raw Uvicorn
        # command is selected by the comparison harness because PR7 lacks src.server.
        (source / "Dockerfile").write_text(Path("Dockerfile").read_text())
        subprocess.run(
            ["docker", "build", "--tag", args.image, str(source)], check=True, timeout=1200
        )
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", args.image]))[0]
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(
                {
                    "server_commit": commit,
                    "server_source_sha256": digest.hexdigest(),
                    "image_digest": image["Id"],
                    "packaging": "candidate Dockerfile; baseline application and frozen dependency lock",
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
