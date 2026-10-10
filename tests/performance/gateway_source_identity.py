"""One runtime-source identity includes the generated database client's inputs."""

import hashlib
from pathlib import Path

RUNTIME_INPUTS = ("uv.lock", "pyproject.toml", "prisma/schema.prisma")


def source_sha256(root: Path = Path(".")) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "src").rglob("*.py")) + [root / name for name in RUNTIME_INPUTS]:
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def image_identity_program(root: Path = Path("/app")) -> str:
    return (
        "import hashlib,pathlib;"
        f"r=pathlib.Path({str(root)!r});d=hashlib.sha256();"
        f"p=sorted((r/'src').rglob('*.py'))+[r/x for x in {RUNTIME_INPUTS!r}];"
        "[(d.update(str(x.relative_to(r)).encode()+b'\\0'+x.read_bytes())) for x in p];"
        "print(d.hexdigest())"
    )
