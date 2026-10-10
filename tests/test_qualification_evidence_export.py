"""Check saved-result preservation and evidence export boundaries."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tests.performance.export_qualification_evidence import export_evidence, verify_files


class QualificationEvidenceExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source"
        self.campaign = self.source / "native-fixture"
        self.stage = self.campaign / "short-500rps"
        self.stage.mkdir(parents=True)
        (self.stage / "samples.jsonl.gz").write_bytes(b"original fixture samples")
        self.report = {
            "target_rate_rps": 500,
            "arrival_window_seconds": 60,
            "started_at": "2026-10-07T00:00:00Z",
            "phase": "short",
            "workload": "fixed-one-token-nonstream-v1",
            "raw": "/old/path/samples.jsonl.gz",
            "target_count": 30000,
            "success_count": 30000,
            "status_counts": {"200": 30000},
            "error_counts": {},
            "generator_dropped_count": 0,
            "latency_seconds": {"mean": 0.05, "p95": 0.08, "p99": 0.12},
            "latency_and_queue": {"in_flight_slope_per_second": 0.0129, "queue_passed": False},
            "accounting_drain": {"passed": True, "seconds": 0.3},
            "accounting_reconciliation": {"passed": True},
            "latency_passed": False,
            "throughput_passed": True,
            "economic_passed": True,
            "passed": False,
            "private_extra": "never publish this unused field",
        }
        self.save_report()
        (self.campaign / "manifest.json").write_text(json.dumps({"release_eligible": False}))

    def save_report(self) -> None:
        (self.stage / "qualification.json").write_text(json.dumps(self.report))

    def test_preserves_failure_and_raw_identity(self) -> None:
        output = self.root / "export"
        index = export_evidence(self.source, output, ("native-fixture",))
        self.assertEqual(index["stage_count"], 1)
        self.assertFalse(index["stages"][0]["passed"])
        self.assertFalse(index["stages"][0]["release_eligible"])
        self.assertEqual(index["stages"][0]["queue_slope_per_second"], 0.0129)
        self.assertNotIn("private_extra", (output / "all-runs.json").read_text())
        self.assertEqual(
            (output / "evidence/native-fixture/short-500rps/samples.jsonl.gz").read_bytes(),
            b"original fixture samples",
        )
        verify_files(output, json.loads((output / "checksums.json").read_text()))

    def test_rejects_existing_or_nested_output(self) -> None:
        for output in (self.source, self.source / "nested"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                export_evidence(self.source, output, ())

    def test_requires_original_raw_samples(self) -> None:
        (self.stage / "samples.jsonl.gz").unlink()
        with self.assertRaises(ValueError):
            export_evidence(self.source, self.root / "export", ())
        self.assertFalse((self.root / "export").exists())

    def test_failed_warmup_is_saved_without_a_measured_stage(self) -> None:
        warmup = {
            **self.report,
            "run_id": "a" * 32,
            "duration_seconds": 60,
            "success_count": 29000,
            "generator_dropped_count": 1000,
        }
        self.report = {
            "measurement_started": False,
            "passed": False,
            "phase": "short",
            "target_rate_rps": 500,
            "warmup": warmup,
        }
        self.save_report()
        (self.stage / "warmup.json").write_text(json.dumps(warmup))
        raw_directory = self.stage / "warmup"
        raw_directory.mkdir()
        raw = raw_directory / f"gateway-load-{'a' * 32}.jsonl.gz"
        raw.write_bytes(b"original preparation samples")
        output = self.root / "export"
        index = export_evidence(self.source, output, ("native-fixture",))
        self.assertEqual(index["stage_count"], 0)
        self.assertEqual(index["preparation_count"], 1)
        self.assertFalse(index["preparations"][0]["passed"])
        self.assertEqual(index["preparations"][0]["generator_dropped_count"], 1000)
        self.assertNotIn("private_extra", (output / "all-runs.json").read_text())
        self.assertIn("not measured qualification stages", (output / "all-runs.md").read_text())
        verify_files(output, json.loads((output / "checksums.json").read_text()))

    def test_preparation_requires_original_raw_samples(self) -> None:
        warmup = {**self.report, "run_id": "b" * 32, "duration_seconds": 60}
        (self.stage / "warmup.json").write_text(json.dumps(warmup))
        with self.assertRaises(ValueError):
            export_evidence(self.source, self.root / "export", ())
        self.assertFalse((self.root / "export").exists())

    def test_unmeasured_stage_cannot_discard_its_failed_preparation(self) -> None:
        self.report = {"measurement_started": False, "passed": False, "warmup": {}}
        self.save_report()
        with self.assertRaisesRegex(ValueError, "preserve its failed preparation"):
            export_evidence(self.source, self.root / "export", ())
        self.assertFalse((self.root / "export").exists())

    def test_rejects_unsupported_rates_and_campaign_paths(self) -> None:
        self.report["target_rate_rps"] = 1000
        self.save_report()
        with self.assertRaises(ValueError):
            export_evidence(self.source, self.root / "export", ())
        self.report["target_rate_rps"] = 500
        self.save_report()
        with self.assertRaises(ValueError):
            export_evidence(self.source, self.root / "export", ("../source",))

    def test_checksum_verification_rejects_changes_and_escape(self) -> None:
        output = self.root / "export"
        export_evidence(self.source, output, ())
        inventory = json.loads((output / "checksums.json").read_text())
        (output / "all-runs.json").write_text("changed")
        with self.assertRaises(ValueError):
            verify_files(output, inventory)
        with self.assertRaises(ValueError):
            verify_files(output, [{"path": "../outside", "sha256": ""}])


if __name__ == "__main__":
    unittest.main()
