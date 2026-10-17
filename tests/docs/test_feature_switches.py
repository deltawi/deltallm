from __future__ import annotations

from pathlib import Path

import yaml

from scripts.docs.generate_feature_switches import SOURCE, generate

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_profiles_match_the_feature_table() -> None:
    _, errors = generate()

    assert errors == []


def test_drifted_profile_is_reported(tmp_path: Path) -> None:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in data["features"]
        if item["setting"] == "general_settings.spend_ingestion_mode"
    )
    entry["profiles"]["small_team"] = "legacy"
    source = tmp_path / "feature_switches.yaml"
    source.write_text(yaml.safe_dump(data), encoding="utf-8")

    _, errors = generate(source)

    assert errors == [
        "general_settings.spend_ingestion_mode: Small team profile is 'outbox', "
        "feature_switches.yaml says 'legacy'"
    ]
