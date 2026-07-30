from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from helpers import DATA_PATH, HISTORY, ROOT, load_json, sha256_path

sys.path.insert(0, str(ROOT / "src"))
import generate  # noqa: E402
import record_snapshot  # noqa: E402

ORIGINAL_ID = "2026-07-28-a5af1ed92d34"
ORIGINAL_SHA = "a5af1ed92d34f47ffbda372e0d028940b7e5aaa485e4a52486dc34f4388dbc64"
ORIGINAL_COMMIT = "533ba99a6567b1a924b7915c55e545fbb0e543a0"


def declared_counts(records: list[dict]) -> dict[str, int]:
    return {"total": len(records), **generate.status_counts(records)}


def refresh(data: dict) -> None:
    generate.refresh_derived_counts(data)


def next_date(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def copy_original_only_history(target: Path) -> None:
    shutil.copytree(HISTORY, target)
    index = load_json(target / "index.json")
    original = next(item for item in index["snapshots"] if item["snapshot_id"] == ORIGINAL_ID)
    index["snapshots"] = [original]
    index["current_snapshot_id"] = ORIGINAL_ID
    (target / "index.json").write_bytes(generate.canonical_json_bytes(index))
    snapshots_dir = target / "snapshots"
    for path in list(snapshots_dir.iterdir()):
        if path.name != ORIGINAL_ID:
            shutil.rmtree(path)


def make_local_source_clone(target: Path) -> Path:
    source_root = target / "source-clone"
    subprocess.run(
        ["git", "clone", "--no-hardlinks", str(ROOT), str(source_root)],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return source_root


def commit_candidate_data(source_root: Path, data_bytes: bytes) -> str:
    (source_root / "data" / "opportunities.json").write_bytes(data_bytes)
    subprocess.run(["git", "add", "data/opportunities.json"], cwd=source_root, check=True, capture_output=True, text=True, timeout=30)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Custody Test",
            "-c",
            "user.email=custody@example.invalid",
            "commit",
            "-m",
            "custody fixture",
        ],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source_root, check=True, capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def candidate_with_added_record(current: dict) -> dict:
    payload = json.loads(json.dumps(current))
    fixture_date = max(record["first_seen"] for record in payload["opportunities"])
    fixture_timestamp = f"{fixture_date}T12:00:00Z"
    payload["page_date"] = fixture_date
    payload["verified_at"] = fixture_timestamp
    for record in payload["opportunities"]:
        record["verified_at"] = fixture_timestamp
        record["verified_date"] = fixture_date
        record["last_verified"] = fixture_date
    new_record = json.loads(json.dumps(payload["opportunities"][0]))
    new_record["id"] = "example-cycle-safe-resource-9999"
    new_record["program"] = "Example Cycle-Safe Resource 9999"
    new_record["first_seen"] = payload["page_date"]
    payload["opportunities"].append(new_record)
    refresh(payload)
    return payload


class TimeMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = load_json(DATA_PATH)
        cls.index = load_json(HISTORY / "index.json")
        cls.original_path = HISTORY / "snapshots" / ORIGINAL_ID / "public_opportunities.json"
        cls.original = load_json(cls.original_path)
        cls.original_ids = {record["id"] for record in cls.original["opportunities"]}

    def test_original_snapshot_preserves_bytes_and_commit(self):
        self.assertEqual(sha256_path(self.original_path), ORIGINAL_SHA)
        original = next(item for item in self.index["snapshots"] if item["snapshot_id"] == ORIGINAL_ID)
        self.assertEqual(original["source"]["commit"], ORIGINAL_COMMIT)
        self.assertEqual(original["canonical_data_sha256"], ORIGINAL_SHA)

    def test_lifecycle_fields_are_required_in_current_but_absent_from_original(self):
        required = {
            "first_seen",
            "last_verified",
            "retired_at",
            "retirement_reason",
            "superseded_by",
            "reactivated_at",
        }
        for record in self.current["opportunities"]:
            self.assertTrue(required <= set(record))
            if record["id"] in self.original_ids:
                self.assertEqual(record["first_seen"], "2026-07-28")
            self.assertLessEqual(record["first_seen"], record["last_verified"])
            self.assertEqual(record["last_verified"], record["verified_date"])
        self.assertFalse(required & set(self.original["opportunities"][0]))
        generate.validate_public_data(self.current)
        generate.validate_public_data(self.original, allow_legacy_lifecycle=True)

    def test_sloan_retirement_is_a_lifecycle_transition_not_absence(self):
        current = {record["id"]: record for record in self.current["opportunities"]}
        original = {record["id"]: record for record in self.original["opportunities"]}
        self.assertIn("sloan-ospo-loi-2023", current)
        self.assertIn("sloan-ospo-loi-2023", original)
        sloan = current["sloan-ospo-loi-2023"]
        self.assertEqual(sloan["status"], "closed")
        self.assertEqual(sloan["retired_at"], "2026-07-29")
        self.assertIn("one-time 2023 call", sloan["retirement_reason"])

    def test_original_manifest_classifies_all_records_as_added(self):
        manifest = load_json(HISTORY / "snapshots" / ORIGINAL_ID / "change_manifest.json")
        self.assertEqual(manifest["previous_snapshot_id"], None)
        self.assertEqual(manifest["counts"], {"added": 32, "changed": 0, "retired": 0, "reactivated": 0, "unchanged": 0})
        self.assertEqual({change["change_type"] for change in manifest["changes"]}, {"added"})

    def test_first_v1_1_pulse_marks_legacy_baseline_unavailable(self):
        latest = self.index["snapshots"][0]
        manifest = load_json(HISTORY / "snapshots" / latest["snapshot_id"] / "change_manifest.json")
        pulse = load_json(HISTORY / "snapshots" / latest["snapshot_id"] / "funding_pulse.json")
        self.assertFalse(manifest["funding_pulse_delta"]["baseline_available"])
        self.assertEqual(manifest["funding_pulse_delta"]["baseline_reason"], "previous_snapshot_has_no_funding_pulse")
        self.assertFalse(pulse["snapshot_delta"]["baseline_available"])
        self.assertEqual(pulse["snapshot_delta"]["baseline_reason"], "previous_snapshot_has_no_funding_pulse")
        previous_id = manifest["previous_snapshot_id"]
        self.assertFalse((HISTORY / "snapshots" / previous_id / "funding_pulse.json").exists())

    def test_manifest_transition_logic_ignores_operational_backfill(self):
        previous = json.loads(json.dumps(self.current))
        current = json.loads(json.dumps(self.current))
        for record in previous["opportunities"]:
            record["first_seen"] = "2026-07-27"
            record["last_verified"] = "2026-07-27"
        manifest = generate.diff_snapshots(previous, current, "test")
        self.assertEqual(manifest["counts"]["changed"], 0)
        self.assertEqual(manifest["counts"]["unchanged"], len(self.current["opportunities"]))

        current = json.loads(json.dumps(self.current))
        target = current["opportunities"][0]
        target["retired_at"] = current["page_date"]
        target["retirement_reason"] = "Official page ended the named call."
        target["status"] = "closed"
        target["deadline_kind"] = "closed"
        target["closing_soon"] = False
        target["application_url"] = None
        target["apply_label"] = None
        manifest = generate.diff_snapshots(previous, current, "test")
        self.assertEqual(manifest["counts"]["retired"], 1)

    def test_date_rollover_verification_stamps_are_not_substantive_changes(self):
        previous = json.loads(json.dumps(self.current))
        current = json.loads(json.dumps(self.current))
        rollover_date = next_date(current["page_date"])
        rollover_timestamp = f"{rollover_date}T12:00:00Z"
        current["page_date"] = rollover_date
        current["verified_at"] = rollover_timestamp
        for record in current["opportunities"]:
            record["verified_at"] = rollover_timestamp
            record["verified_date"] = rollover_date
            record["last_verified"] = rollover_date
        refresh(current)
        manifest = generate.diff_snapshots(previous, current, "test")
        self.assertEqual({key: manifest["counts"][key] for key in ("added", "changed", "retired", "reactivated", "unchanged")}, {"added": 0, "changed": 0, "retired": 0, "reactivated": 0, "unchanged": len(self.current["opportunities"])})
        self.assertTrue(all(not change["changed_fields"] for change in manifest["changes"]))
        first_change = manifest["changes"][0]
        self.assertNotEqual(first_change["previous_record_sha256"], first_change["current_record_sha256"])

    def test_recorder_rejects_uncommitted_candidate_data_without_matching_custody(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            history = temp_root / "history"
            shutil.copytree(HISTORY, history)
            data = temp_root / "opportunities.json"
            payload = candidate_with_added_record(self.current)
            data.write_bytes(generate.canonical_json_bytes(payload))
            with self.assertRaisesRegex(record_snapshot.RecorderError, "does not reproduce canonical data"):
                record_snapshot.record_snapshot(
                    data_path=data,
                    history_dir=history,
                    source_commit="28e802bf4df2a7653a790e0546e99a838c850dab",
                )
        try:
            scratch.rmdir()
        except OSError:
            pass

    def test_recorder_appends_committed_candidate_data_with_real_custody(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            history = temp_root / "history"
            shutil.copytree(HISTORY, history)
            before_original = (history / "snapshots" / ORIGINAL_ID / "public_opportunities.json").read_bytes()
            before_current = (history / "snapshots" / self.index["current_snapshot_id"] / "public_opportunities.json").read_bytes()
            data = temp_root / "opportunities.json"
            payload = candidate_with_added_record(self.current)
            data.write_bytes(generate.canonical_json_bytes(payload))
            source_root = make_local_source_clone(temp_root)
            source_commit = commit_candidate_data(source_root, data.read_bytes())

            snapshot_id = record_snapshot.record_snapshot(
                data_path=data,
                history_dir=history,
                source_commit=source_commit,
                source_repo_root=source_root,
            )
            index, snapshots = generate.load_history(history, repo_root=source_root)
            self.assertEqual(index["current_snapshot_id"], snapshot_id)
            self.assertEqual(index["snapshots"][0]["source"]["commit"], source_commit)
            self.assertEqual(index["snapshots"][0]["record_count"], len(self.current["opportunities"]) + 1)
            self.assertEqual(len(snapshots), len(self.index["snapshots"]) + 1)
            pulse = load_json(history / "snapshots" / snapshot_id / "funding_pulse.json")
            self.assertEqual(pulse["provenance"]["source_git"]["commit"], source_commit)
            self.assertEqual(pulse["provenance"]["canonical_data_sha256"], index["snapshots"][0]["canonical_data_sha256"])
            self.assertEqual((history / "snapshots" / ORIGINAL_ID / "public_opportunities.json").read_bytes(), before_original)
            self.assertEqual((history / "snapshots" / self.index["current_snapshot_id"] / "public_opportunities.json").read_bytes(), before_current)
        try:
            scratch.rmdir()
        except OSError:
            pass

    def test_load_history_rejects_tampered_pulse_provenance(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            history = temp_root / "history"
            shutil.copytree(HISTORY, history)
            snapshot_id = self.index["current_snapshot_id"]
            pulse_path = history / "snapshots" / snapshot_id / "funding_pulse.json"
            pulse = load_json(pulse_path)
            pulse["provenance"]["source_git"]["commit"] = "0" * 40
            pulse_path.write_bytes(generate.canonical_json_bytes(pulse))
            index = load_json(history / "index.json")
            index["snapshots"][0]["funding_pulse_sha256"] = sha256_path(pulse_path)
            (history / "index.json").write_bytes(generate.canonical_json_bytes(index))
            with self.assertRaisesRegex(ValueError, "funding pulse provenance source commit mismatch"):
                generate.load_history(history)
        try:
            scratch.rmdir()
        except OSError:
            pass

    def test_generation_rejects_tampered_pulse_bytes_without_index_hash_update(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            history = temp_root / "history"
            shutil.copytree(HISTORY, history)
            snapshot_id = self.index["current_snapshot_id"]
            pulse_path = history / "snapshots" / snapshot_id / "funding_pulse.json"
            pulse = load_json(pulse_path)
            pulse["warnings"].append("Tampered warning.")
            pulse_path.write_bytes(generate.canonical_json_bytes(pulse))
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "generate.py"),
                    "--data",
                    str(DATA_PATH),
                    "--history-dir",
                    str(history),
                    "--output",
                    str(temp_root / "dist"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("funding pulse hash mismatch", result.stderr)
        try:
            scratch.rmdir()
        except OSError:
            pass

    def test_finalizer_rejects_wrong_custody_commit_without_metadata_writes(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            history = temp_root / "history"
            shutil.copytree(HISTORY, history)
            snapshot_id = self.index["current_snapshot_id"]
            index_path = history / "index.json"
            pulse_path = history / "snapshots" / snapshot_id / "funding_pulse.json"
            before_index = index_path.read_bytes()
            before_pulse = pulse_path.read_bytes()
            with self.assertRaisesRegex(record_snapshot.RecorderError, "does not reproduce the existing snapshot bytes"):
                record_snapshot.finalize_snapshot_provenance(
                    history_dir=history,
                    snapshot_id=snapshot_id,
                    source_commit="e4d956b54a6a73f8a4a75dcf14cb41d24c18bfde",
                )
            self.assertEqual(index_path.read_bytes(), before_index)
            self.assertEqual(pulse_path.read_bytes(), before_pulse)
        try:
            scratch.rmdir()
        except OSError:
            pass

    def test_missing_id_refusal(self):
        previous = json.loads(json.dumps(self.current))
        current = json.loads(json.dumps(self.current))
        current["opportunities"] = current["opportunities"][1:]
        refresh(current)
        with self.assertRaisesRegex(ValueError, "remove previously published IDs"):
            generate.diff_snapshots(previous, current, "test")

    def test_production_build_refuses_current_latest_drift(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            data = temp_root / "opportunities.json"
            payload = json.loads(json.dumps(self.current))
            payload["verified_at"] = "2099-01-01T00:00:00Z"
            data.write_bytes(generate.canonical_json_bytes(payload))
            history = temp_root / "history"
            shutil.copytree(HISTORY, history)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "generate.py"),
                    "--data",
                    str(data),
                    "--history-dir",
                    str(history),
                    "--output",
                    str(temp_root / "dist"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("production build refused", result.stderr)
        try:
            scratch.rmdir()
        except OSError:
            pass

    def test_recorder_refuses_overwrite_and_missing_ids(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            overwrite_history = temp_root / "overwrite-history"
            shutil.copytree(HISTORY, overwrite_history)
            overwrite_data = temp_root / "overwrite-opportunities.json"
            overwrite_payload = load_json(DATA_PATH)
            overwrite_payload["verified_at"] = "2099-01-01T00:00:00Z"
            overwrite_data.write_bytes(generate.canonical_json_bytes(overwrite_payload))
            snapshot_id = generate.snapshot_id_for(sha256_path(overwrite_data), overwrite_payload["page_date"])
            existing = overwrite_history / "snapshots" / snapshot_id
            existing.mkdir(exist_ok=True)
            with self.assertRaisesRegex(record_snapshot.RecorderError, "will not be overwritten"):
                record_snapshot.record_snapshot(
                    data_path=overwrite_data,
                    history_dir=overwrite_history,
                    source_commit="28e802bf4df2a7653a790e0546e99a838c850dab",
                )

            removal_history = temp_root / "removal-history"
            shutil.copytree(HISTORY, removal_history)
            data = temp_root / "removal-opportunities.json"
            data.write_bytes(DATA_PATH.read_bytes())
            payload = load_json(data)
            payload["opportunities"] = payload["opportunities"][1:]
            refresh(payload)
            data.write_bytes(generate.canonical_json_bytes(payload))
            with self.assertRaisesRegex(ValueError, "remove previously published IDs"):
                record_snapshot.record_snapshot(
                    data_path=data,
                    history_dir=removal_history,
                    source_commit="28e802bf4df2a7653a790e0546e99a838c850dab",
                )
        try:
            scratch.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
