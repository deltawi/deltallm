from pathlib import Path

from scripts.check_container_contract import railway_dockerfile
from scripts.check_lifecycle_image import MIGRATION_CHECK


def test_runtime_selects_bundled_native_migration_cli_without_changing_build_client():
    source = Path("Dockerfile").read_text()
    runtime = source.rsplit("FROM base\n", 1)[1]
    assert "ENV PATH=/opt/prisma/binaries/node_modules/.bin:$PATH" in runtime
    assert "RUN prisma generate --schema=./prisma/schema.prisma && prisma py fetch" in source
    assert 'shutil.which("prisma")' in MIGRATION_CHECK


def test_railway_container_retains_the_same_native_migration_cli():
    assert Path("deploy/railway/Dockerfile").read_text() == railway_dockerfile()
