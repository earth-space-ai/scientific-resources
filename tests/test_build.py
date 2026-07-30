from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import DATA_PATH, DIST, HISTORY, PROFILES_PATH, ROOT, HTMLFacts, load_json, sha256_path, tree_hashes

EXPECTED_URLS = {
    "primary": "https://earth-space-ai.org/scientific-resources",
    "mirror": "https://huangzesen.github.io/scientific-resources/",
}


class BuildParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json(DATA_PATH)
        cls.profiles = load_json(PROFILES_PATH)["profiles"]
        cls.history_index = load_json(HISTORY / "index.json")
        cls.current_snapshot_id = cls.history_index["current_snapshot_id"]
        cls.current_snapshot_summary = next(
            item for item in cls.history_index["snapshots"] if item["snapshot_id"] == cls.current_snapshot_id
        )
        cls.current_snapshot_path = HISTORY / "snapshots" / cls.current_snapshot_id / "public_opportunities.json"
        cls.current_snapshot = load_json(cls.current_snapshot_path)

    def html(self, profile):
        return (DIST / profile / "index.html").read_text(encoding="utf-8")

    def facts(self, profile):
        parser = HTMLFacts()
        parser.feed(self.html(profile))
        return parser

    def test_expected_artifacts_exist(self):
        expected_names = {"index.html", "public_opportunities.json", "funding_pulse.json", "provenance.json"}
        for profile in EXPECTED_URLS:
            profile_dir = DIST / profile
            self.assertTrue(profile_dir.is_dir())
            self.assertEqual({path.name for path in profile_dir.iterdir() if path.is_file()}, expected_names)
            self.assertTrue((profile_dir / "snapshots" / "index.json").is_file())
            self.assertEqual(
                (profile_dir / "funding_pulse.json").read_bytes(),
                (HISTORY / "snapshots" / self.current_snapshot_id / "funding_pulse.json").read_bytes(),
            )

    def test_both_standalone_datasets_equal_canonical_bytes(self):
        canonical = self.current_snapshot_path.read_bytes()
        for profile in EXPECTED_URLS:
            self.assertEqual((DIST / profile / "public_opportunities.json").read_bytes(), canonical)
        self.assertEqual(
            sha256_path(DIST / "primary" / "public_opportunities.json"),
            sha256_path(DIST / "mirror" / "public_opportunities.json"),
        )

    def test_embedded_json_exactly_matches_canonical_object(self):
        for profile in EXPECTED_URLS:
            self.assertEqual(
                self.facts(profile).embedded_data,
                self.current_snapshot,
            )

    def test_host_profile_metadata(self):
        for profile, expected_url in EXPECTED_URLS.items():
            facts = self.facts(profile)
            body = facts.attrs_by_tag["body"][0]
            self.assertEqual(body.get("data-site-profile"), profile)
            canonical = [item for item in facts.attrs_by_tag["link"] if item.get("rel") == "canonical"]
            alternate = [item for item in facts.attrs_by_tag["link"] if item.get("rel") == "alternate"]
            og_url = [item for item in facts.attrs_by_tag["meta"] if item.get("property") == "og:url"]
            self.assertEqual([item["href"] for item in canonical], [expected_url])
            other = "mirror" if profile == "primary" else "primary"
            self.assertEqual([item["href"] for item in alternate], [EXPECTED_URLS[other]])
            self.assertEqual([item["content"] for item in og_url], [expected_url])
            self.assertIn(self.profiles[profile]["alternate_label"], self.html(profile))

    def test_record_content_and_ids_match_across_hosts(self):
        expected_ids = {"card-" + record["id"] for record in self.current_snapshot["opportunities"]}
        parsed = {}
        for profile in EXPECTED_URLS:
            facts = self.facts(profile)
            article_ids = {item["id"] for item in facts.attrs_by_tag["article"]}
            self.assertEqual(article_ids, expected_ids)
            parsed[profile] = facts.embedded_data["opportunities"]
        self.assertEqual(parsed["primary"], parsed["mirror"])

    def test_history_outputs_are_byte_identical_across_hosts(self):
        primary = {
            path.relative_to(DIST / "primary").as_posix(): path.read_bytes()
            for path in sorted((DIST / "primary" / "snapshots").rglob("*"))
            if path.is_file()
        }
        mirror = {
            path.relative_to(DIST / "mirror").as_posix(): path.read_bytes()
            for path in sorted((DIST / "mirror" / "snapshots").rglob("*"))
            if path.is_file()
        }
        self.assertEqual(primary, mirror)

    def test_scrubbed_provenance_manifests(self):
        for profile in EXPECTED_URLS:
            profile_dir = DIST / profile
            manifest = load_json(profile_dir / "provenance.json")
            self.assertEqual(manifest["manifest_version"], "1.1.0")
            self.assertEqual(manifest["snapshot_date"], self.current_snapshot["page_date"])
            self.assertEqual(manifest["profile"]["id"], profile)
            self.assertEqual(manifest["profile"]["canonical_url"], EXPECTED_URLS[profile])
            self.assertEqual(manifest["canonical_data"]["logical_name"], "data/opportunities.json")
            self.assertEqual(
                manifest["canonical_data"]["sha256"],
                self.current_snapshot_summary["canonical_data_sha256"],
            )
            self.assertEqual(manifest["canonical_data"]["record_count"], self.current_snapshot_summary["record_count"])
            self.assertEqual(manifest["canonical_data"]["status_counts"],
                             self.current_snapshot_summary["status_counts"])
            for logical_name, digest in manifest["build_inputs"].items():
                self.assertFalse(Path(logical_name).is_absolute())
                self.assertEqual(digest, sha256_path(ROOT / logical_name))
            for name, digest in manifest["outputs"].items():
                self.assertEqual(digest, sha256_path(profile_dir / name))
            self.assertIn("snapshots/index.json", manifest["outputs"])
            serialized = json.dumps(manifest)
            self.assertNotIn("/" + "Users" + "/", serialized)
            self.assertNotIn("generated_at", serialized)

    def test_rebuild_refuses_current_latest_drift_without_changing_dist(self):
        before = tree_hashes(DIST)
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            temp_root = Path(temp)
            data_path = temp_root / "opportunities.json"
            payload = load_json(DATA_PATH)
            payload["verified_at"] = "2099-01-01T00:00:00Z"
            data_path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            history_dir = temp_root / "history"
            shutil.copytree(HISTORY, history_dir)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "generate.py"),
                    "--data",
                    str(data_path),
                    "--history-dir",
                    str(history_dir),
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
        self.assertIn("production build refused", result.stderr)
        self.assertEqual(tree_hashes(DIST), before)
        try:
            scratch.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
