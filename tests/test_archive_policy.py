from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import generate  # noqa: E402


class ArchivePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads((ROOT / "data" / "opportunities.json").read_text(encoding="utf-8"))
        cls.by_id = {record["id"]: record for record in cls.data["opportunities"]}

    def candidate_data(self) -> dict:
        return copy.deepcopy(self.data)

    @staticmethod
    def recount(data: dict) -> None:
        generate.refresh_derived_counts(data)

    @staticmethod
    def record(data: dict, record_id: str) -> dict:
        return next(record for record in data["opportunities"] if record["id"] == record_id)

    def test_current_dataset_matches_reviewed_policy_decision(self) -> None:
        generate.validate_public_data(self.data)
        self.assertEqual(self.data["counts"], {"total": 70, "open": 50, "upcoming": 3, "closed": 17})
        self.assertEqual(self.data["view_counts"]["curated"], {"total": 54, "open": 38, "upcoming": 1, "closed": 15})
        self.assertEqual(self.data["relevance_counts"]["unrelated"], 16)
        self.assertEqual(sum(record["closing_soon"] for record in self.data["opportunities"]), 0)
        self.assertGreaterEqual(sum(bool(record["application_url"]) for record in self.data["opportunities"]), 37)
        expected_added_nsf = {
            "nsf-shine-22-570": ("fixed", "2026-10-07"),
            "nsf-plasma-physics-23-615": ("fixed", "2026-11-16"),
            "nsf-gem-22-537": ("fixed", "2026-09-30"),
            "nsf-geospace-cluster-ags-gc": ("rolling", None),
            "nsf-aag-22-624": ("fixed", "2026-11-16"),
            "nsf-cedar-25-510": ("fixed", "2027-03-03"),
        }
        for record_id, (deadline_kind, next_deadline) in expected_added_nsf.items():
            record = self.by_id[record_id]
            self.assertEqual(record["status"], "open")
            self.assertEqual(record["deadline_kind"], deadline_kind)
            self.assertEqual(record["next_deadline"], next_deadline)
            self.assertEqual(record["first_seen"], "2026-07-29")
            self.assertEqual(record["application_url"], "https://www.research.gov/")
            self.assertIsNone(record["retired_at"])
        self.assertIn("target date, not a hard deadline", self.by_id["nsf-shine-22-570"]["endpoint_note"])
        self.assertIn("not a Plasma-specific allocation", self.by_id["nsf-plasma-physics-23-615"]["endpoint_note"])
        expected_retired = {
            "access-maximize-2026-july",
            "anthropic-rare-disease-research-grants-2026",
            "google-gara-eftqc-2026",
            "google-gara-quantum-computing-security-2026",
            "schmidt-multi-agent-ai-safety-2026",
        }
        for record_id in expected_retired:
            record = self.by_id[record_id]
            self.assertEqual(record["status"], "closed")
            self.assertEqual(record["deadline_kind"], "closed")
            self.assertEqual(record["retired_at"], "2026-07-29")
            self.assertIsNotNone(record["next_deadline"])
            self.assertIsNone(record["application_url"])
            self.assertIn("15-calendar-day", record["retirement_reason"])
            self.assertIn("not a sponsor-closure claim", record["endpoint_note"])
        self.assertEqual(self.by_id["eurohpc-development-access-2026"]["next_deadline"], "2026-09-01")
        self.assertEqual(self.by_id["eurohpc-development-access-2026"]["status"], "open")
        nasa = self.by_id["nasa-roses-2025-umbrella"]
        self.assertEqual(nasa["status"], "open")
        self.assertEqual(nasa["deadline_kind"], "tbd")
        self.assertIsNone(nasa["next_deadline"])
        self.assertIn("December 31, 2026 solicitation close", nasa["deadline"])
        self.assertIn("program-element dates through August", nasa["deadline"])
        self.assertIn("D.6 Astrophysics Research and Analysis with a mandatory June 25, 2026 NOI", nasa["endpoint_note"])
        self.assertIn("does not call the notice mandatory", nasa["endpoint_note"])
        self.assertNotIn("each had a mandatory", nasa["endpoint_note"])
        self.assertIn(
            "https://nspires.nasaprs.com/external/solicitations/summary.do?solId=%7B9455D565-3411-0574-BABC-255C7945CD08%7D&path=&method=init",
            nasa["official_source_urls"],
        )

    def test_archive_fence_is_inclusive_and_group_neutral(self) -> None:
        snapshot = date.fromisoformat(self.data["page_date"])
        records_by_group = {
            "credits": "google-cloud-research-credits-5000",
            "hpc": "eurohpc-development-access-2026",
            "grants": "nsf-26-512-ai-datasets",
        }
        cases = [
            (15, "open", True),
            (16, "open", False),
            (15, "upcoming", True),
            (-1, "open", True),
        ]
        for group, record_id in records_by_group.items():
            for offset, status, should_reject in cases:
                with self.subTest(group=group, offset=offset, status=status):
                    data = self.candidate_data()
                    record = self.record(data, record_id)
                    record["status"] = status
                    record["deadline_kind"] = "fixed"
                    record["next_deadline"] = (snapshot + timedelta(days=offset)).isoformat()
                    record["closing_soon"] = bool(status == "open" and offset <= generate.CLOSING_SOON_DAYS)
                    if status != "open":
                        record["application_url"] = None
                        record["apply_label"] = None
                    self.recount(data)
                    if should_reject:
                        with self.assertRaisesRegex(ValueError, "archive fence"):
                            generate.validate_public_data(data)
                    else:
                        generate.validate_public_data(data)

    def test_rolling_and_tbd_records_without_machine_date_are_unaffected(self) -> None:
        data = self.candidate_data()
        record = self.record(data, "nasa-roses-2025-umbrella")
        record["deadline_kind"] = "tbd"
        record["next_deadline"] = None
        record["closing_soon"] = False
        generate.validate_public_data(data)

    def test_current_fixed_record_requires_machine_date(self) -> None:
        data = self.candidate_data()
        record = self.record(data, "eurohpc-development-access-2026")
        record["deadline_kind"] = "fixed"
        record["next_deadline"] = None
        record["closing_soon"] = False
        with self.assertRaisesRegex(ValueError, "machine-readable"):
            generate.validate_public_data(data)

    def test_archived_record_may_preserve_future_official_deadline(self) -> None:
        record = self.by_id["google-gara-eftqc-2026"]
        self.assertEqual(record["status"], "closed")
        self.assertGreater(date.fromisoformat(record["next_deadline"]), date.fromisoformat(self.data["page_date"]))
        generate.validate_public_data(self.data)

    def test_lifecycle_dates_must_not_exceed_last_verification(self) -> None:
        data = self.candidate_data()
        record = self.record(data, "google-gara-eftqc-2026")
        record["retired_at"] = "2026-07-31"
        with self.assertRaisesRegex(ValueError, "between first_seen and last_verified"):
            generate.validate_public_data(data)

    def test_second_retirement_preserves_prior_reactivation(self) -> None:
        previous = copy.deepcopy(self.by_id["google-gara-eftqc-2026"])
        previous.update(
            {
                "status": "open",
                "deadline_kind": "fixed",
                "next_deadline": "2026-09-01",
                "closing_soon": False,
                "application_url": previous["official_source_urls"][0],
                "apply_label": "Apply",
                "retired_at": None,
                "retirement_reason": None,
                "reactivated_at": "2026-07-28",
            }
        )
        current = copy.deepcopy(previous)
        current.update(
            {
                "status": "closed",
                "deadline_kind": "closed",
                "closing_soon": False,
                "application_url": None,
                "apply_label": None,
                "retired_at": self.data["page_date"],
                "retirement_reason": "Archived again after a verified later cycle entered the lead-time fence.",
            }
        )
        generate.validate_lifecycle(current, "current")
        generate.validate_lifecycle_transition(previous, current, self.data["page_date"])
        self.assertEqual(current["reactivated_at"], "2026-07-28")

    def test_retirement_cannot_fabricate_or_rewrite_reactivation(self) -> None:
        previous = copy.deepcopy(self.by_id["google-gara-eftqc-2026"])
        previous.update(
            {
                "status": "open",
                "deadline_kind": "fixed",
                "next_deadline": "2026-09-01",
                "closing_soon": False,
                "application_url": previous["official_source_urls"][0],
                "apply_label": "Apply",
                "retired_at": None,
                "retirement_reason": None,
                "reactivated_at": None,
            }
        )

        fabricated = copy.deepcopy(previous)
        fabricated.update(
            {
                "status": "closed",
                "deadline_kind": "closed",
                "closing_soon": False,
                "application_url": None,
                "apply_label": None,
                "retired_at": self.data["page_date"],
                "retirement_reason": "Test retirement.",
                "reactivated_at": self.data["page_date"],
            }
        )
        with self.assertRaisesRegex(ValueError, "cannot fabricate or rewrite"):
            generate.validate_lifecycle_transition(previous, fabricated, self.data["page_date"])

        previously_reactivated = copy.deepcopy(previous)
        previously_reactivated["reactivated_at"] = "2026-07-28"
        rewritten = copy.deepcopy(fabricated)
        rewritten["reactivated_at"] = self.data["page_date"]
        with self.assertRaisesRegex(ValueError, "cannot fabricate or rewrite"):
            generate.validate_lifecycle_transition(previously_reactivated, rewritten, self.data["page_date"])

    def test_status_only_pseudo_transitions_are_rejected(self) -> None:
        previous = copy.deepcopy(self.by_id["eurohpc-development-access-2026"])
        current = copy.deepcopy(previous)
        current.update(
            {
                "status": "closed",
                "deadline_kind": "closed",
                "closing_soon": False,
                "application_url": None,
                "apply_label": None,
            }
        )
        with self.assertRaisesRegex(ValueError, "requires retired_at"):
            generate.validate_lifecycle_transition(previous, current, self.data["page_date"])

        previous = copy.deepcopy(self.by_id["google-gara-eftqc-2026"])
        current = copy.deepcopy(previous)
        current.update(
            {
                "status": "open",
                "deadline_kind": "fixed",
                "next_deadline": "2026-09-01",
                "application_url": previous["official_source_urls"][0],
                "apply_label": "Apply",
                "retired_at": None,
                "retirement_reason": None,
                "reactivated_at": None,
            }
        )
        with self.assertRaisesRegex(ValueError, "reactivation date must be newly set"):
            generate.validate_lifecycle_transition(previous, current, self.data["page_date"])

    def test_manifest_classifies_real_retirement_and_reactivation(self) -> None:
        base = self.candidate_data()
        current = copy.deepcopy(base)
        record = self.record(current, "eurohpc-development-access-2026")
        record.update(
            {
                "status": "closed",
                "deadline_kind": "closed",
                "closing_soon": False,
                "application_url": None,
                "apply_label": None,
                "retired_at": self.data["page_date"],
                "retirement_reason": "Test retirement.",
            }
        )
        self.recount(current)
        self.recount(current)
        manifest = generate.diff_snapshots(base, current, "2026-07-30-000000000000")
        change = next(item for item in manifest["changes"] if item["id"] == record["id"])
        self.assertEqual(change["change_type"], "retired")

        reactivated = copy.deepcopy(current)
        record = self.record(reactivated, "eurohpc-development-access-2026")
        record.update(
            {
                "status": "open",
                "deadline_kind": "fixed",
                "next_deadline": "2026-09-01",
                "closing_soon": False,
                "application_url": self.by_id["eurohpc-development-access-2026"]["application_url"],
                "apply_label": self.by_id["eurohpc-development-access-2026"]["apply_label"],
                "retired_at": None,
                "retirement_reason": None,
                "reactivated_at": self.data["page_date"],
            }
        )
        self.recount(reactivated)
        self.recount(reactivated)
        manifest = generate.diff_snapshots(current, reactivated, "2026-07-30-111111111111")
        change = next(item for item in manifest["changes"] if item["id"] == record["id"])
        self.assertEqual(change["change_type"], "reactivated")

    def test_2026_07_29_lead_time_policy_release_is_immutable(self) -> None:
        snapshot_id = "2026-07-29-b129c990e65b"
        history = ROOT / "data" / "history"
        index = json.loads((history / "index.json").read_text(encoding="utf-8"))
        summary = next(item for item in index["snapshots"] if item["snapshot_id"] == snapshot_id)
        self.assertEqual(summary["canonical_data_sha256"], "b129c990e65b90848743a12253fb63e5470eaa0325e501b9815bf2093b9dabcf")
        self.assertEqual(summary["record_count"], 48)
        self.assertEqual(summary["status_counts"], {"open": 32, "upcoming": 1, "closed": 15})

        manifest = json.loads(
            (history / "snapshots" / snapshot_id / "change_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["previous_snapshot_id"], "2026-07-29-cfe70c851e31")
        self.assertEqual(
            manifest["previous_data_sha256"],
            "cfe70c851e31e17e2a9a0a78b9c093ad809928f9a4787da9fe2d2e2be015aa6b",
        )
        self.assertEqual(
            manifest["counts"],
            {"added": 0, "changed": 2, "retired": 5, "reactivated": 0, "unchanged": 41},
        )
        material = {
            item["id"]: item["change_type"]
            for item in manifest["changes"]
            if item["change_type"] != "unchanged"
        }
        self.assertEqual(
            material,
            {
                "access-maximize-2026-july": "retired",
                "anthropic-rare-disease-research-grants-2026": "retired",
                "eurohpc-development-access-2026": "changed",
                "google-gara-eftqc-2026": "retired",
                "google-gara-quantum-computing-security-2026": "retired",
                "nasa-roses-2025-umbrella": "changed",
                "schmidt-multi-agent-ai-safety-2026": "retired",
            },
        )

        immutable_artifacts = {
            "2026-07-28-a5af1ed92d34/public_opportunities.json": "a5af1ed92d34f47ffbda372e0d028940b7e5aaa485e4a52486dc34f4388dbc64",
            "2026-07-28-a5af1ed92d34/change_manifest.json": "32fd79e212dd77fe6c5f144f25e8d72fcd7ef577d627c5af4a4fd75311061915",
            "2026-07-29-9a003fb345f9/public_opportunities.json": "9a003fb345f956defe7198325774f2934d4c3d9d53c268955701075fd07bf866",
            "2026-07-29-9a003fb345f9/change_manifest.json": "784ee37a2087925f062a747958472c3967e1012ab93d203b1f1e9e04c22f98c6",
            "2026-07-29-cfe70c851e31/public_opportunities.json": "cfe70c851e31e17e2a9a0a78b9c093ad809928f9a4787da9fe2d2e2be015aa6b",
            "2026-07-29-cfe70c851e31/change_manifest.json": "ed3cae7feebb2d7e9bd595a9ea9b3bc4243e8e500faa6cd938bd6e9ed1e5cc76",
            "2026-07-29-b129c990e65b/public_opportunities.json": "b129c990e65b90848743a12253fb63e5470eaa0325e501b9815bf2093b9dabcf",
            "2026-07-29-b129c990e65b/change_manifest.json": "1994645fc2ca3b06e8bb1754bdf6ee8be1e25d6079e839529efb858d5c4b4ff3",
        }
        for relative_path, expected_sha in immutable_artifacts.items():
            with self.subTest(relative_path=relative_path):
                digest = hashlib.sha256((history / "snapshots" / relative_path).read_bytes()).hexdigest()
                self.assertEqual(digest, expected_sha)

    def test_server_card_visibly_explains_archive_and_has_no_apply_control(self) -> None:
        record = self.by_id["google-gara-eftqc-2026"]
        card = generate.card_html(record, archived=True)
        self.assertIn("Archived 2026-07-29:", card)
        self.assertIn("15-calendar-day lead-time policy", card)
        self.assertIn(record["deadline"], card)
        self.assertNotIn("apply-link", card)
        self.assertIn("retirement", card)

    def test_browser_renderer_contains_matching_archive_contract(self) -> None:
        template = (ROOT / "src" / "template.html").read_text(encoding="utf-8")
        self.assertIn('closed: "Archived"', template)
        self.assertIn('data-status="closed"', template)
        self.assertIn('Archived ({{CLOSED_COUNT}})', template)
        self.assertIn('archiveSummary.addEventListener("click"', template)
        self.assertIn('archiveNav.addEventListener("click"', template)
        self.assertIn('selectedStatus === "closed"', template)
        self.assertIn('record.retirement_reason || ""', template)
        self.assertIn("Archived \" + record.retired_at + \":\"", template)
        self.assertIn("record.application_url && !archived", template)
        self.assertIn("15 calendar days", template)
        self.assertIn("function snapshotLabel(item)", template)
        self.assertIn("snapshotLabel(item) + (item.snapshot_id", template)
        self.assertIn('"Showing reviewed snapshot " + snapshotLabel(item)', template)
        self.assertIn("No actionable records were marked as closing soon", template)
        self.assertIn("No actionable records were marked as closing soon", generate.closing_soon_html(self.data["opportunities"]))


if __name__ == "__main__":
    unittest.main()
