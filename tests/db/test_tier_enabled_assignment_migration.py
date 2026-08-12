from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "prisma"
    / "migrations"
    / "20260810120000_tier_enabled_assignment_invariants"
    / "migration.sql"
)


def test_enabled_tier_assignment_migration_guards_both_mutation_directions() -> None:
    sql = MIGRATION.read_text()

    assert "enabled assignments reference disabled tiers" in sql
    assert 'SELECT t."tier_id", t."enabled"' in sql
    assert "enabled tier assignments require an enabled tier" in sql
    assert 'CREATE TRIGGER "deltallm_tier_disable_assignment_guard"' in sql
    assert "cannot disable tier while enabled organization assignments exist" in sql
    assert 'a."ends_at" IS NULL OR a."ends_at" > NOW()' in sql
    assert 'a."starts_at"' not in sql
