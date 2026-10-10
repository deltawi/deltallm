from pathlib import Path
import subprocess
import sys
import tomllib
import yaml

from scripts.check_container_contract import railway_dockerfile
from scripts.check_lifecycle_image import (
    CHECK,
    DATABASE_IMPORT_CHECK,
    MIGRATION_CHECK,
    image_smoke_checks,
)


def test_generated_runtime_dependencies_match_the_frozen_lock():
    result = subprocess.run(
        [sys.executable, "scripts/check_container_contract.py"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_production_chart_validation_has_explicit_provider_fixture_capacity():
    workflow = yaml.safe_load(Path(".github/workflows/release-images.yml").read_text())
    scripts = [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]
    validation = next(script for script in scripts if "helm lint /tmp/deltallm-chart" in script)
    for section in validation.split("helm ")[1:]:
        if "-f /tmp/deltallm-chart/values-production.yaml" in section:
            assert "-f /tmp/deltallm-chart/values-capacity-fixture.yaml" in section


def test_runtime_async_detector_is_locked_and_checked_in_the_actual_image():
    project = tomllib.loads(Path("pyproject.toml").read_text())
    assert "sniffio==1.3.1" in project["project"]["dependencies"]
    assert "import sniffio" in CHECK


def test_runtime_selects_bundled_native_migration_cli_without_changing_build_client():
    source = Path("Dockerfile").read_text()
    runtime = source.rsplit("FROM base\n", 1)[1]
    assert "ENV PATH=/opt/prisma/binaries/node_modules/.bin:$PATH" in runtime
    assert "RUN prisma generate --schema=./prisma/schema.prisma && prisma py fetch" in source
    assert 'shutil.which("prisma")' in MIGRATION_CHECK


def test_railway_container_retains_the_same_native_migration_cli():
    assert Path("deploy/railway/Dockerfile").read_text() == railway_dockerfile()


def test_native_cli_fetches_its_engine_during_the_image_build():
    source = Path("Dockerfile").read_text()
    generate = source.index("RUN prisma generate")
    fetch_python_engine = source.index("prisma py fetch", generate)
    fetch_native_engine = source.index(
        "/opt/prisma/binaries/node_modules/.bin/prisma -v", fetch_python_engine
    )
    assert generate < fetch_python_engine < fetch_native_engine < source.index("FROM base\n")
    assert '["prisma", "-v"]' in CHECK


def test_generated_client_uses_recursive_types_and_build_time_bytecode():
    schema = Path("prisma/schema.prisma").read_text()
    assert "recursive_type_depth        = -1" in schema.split("datasource db", 1)[0]
    source = Path("Dockerfile").read_text()
    generate = source.index("RUN prisma generate")
    compile_client = source.index(
        "RUN python -m compileall -q /opt/venv/lib/python3.11/site-packages/prisma"
    )
    assert generate < compile_client < source.index("FROM base\n")


def test_actual_api_and_native_role_imports_have_a_one_gib_image_gate():
    checks = {check.label: check for check in image_smoke_checks(False)}
    check = checks["database-import"]
    assert check.memory == "1g" and check.expected == 0
    assert check.command == ["python", "-c", DATABASE_IMPORT_CHECK]
    assert "from prisma import Prisma" in DATABASE_IMPORT_CHECK
    assert "from src.main import app" in DATABASE_IMPORT_CHECK
    assert "from src.bootstrap.accounting_worker_app" in DATABASE_IMPORT_CHECK
