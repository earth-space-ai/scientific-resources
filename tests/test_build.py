from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import DATA_PATH, DIST, PROFILES_PATH, ROOT, HTMLFacts, load_json, sha256_path, tree_hashes

EXPECTED_URLS = {
    "primary": "https://earth-space-ai.org/scientific-resources",
    "mirror": "https://huangzesen.github.io/scientific-resources/",
}


class BuildParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json(DATA_PATH)
        cls.profiles = load_json(PROFILES_PATH)["profiles"]

    def html(self, profile):
        return (DIST / profile / "index.html").read_text(encoding="utf-8")

    def facts(self, profile):
        parser = HTMLFacts()
        parser.feed(self.html(profile))
        return parser

    def test_expected_artifacts_exist(self):
        expected_names = {"index.html", "public_opportunities.json", "provenance.json"}
        for profile in EXPECTED_URLS:
            profile_dir = DIST / profile
            self.assertTrue(profile_dir.is_dir())
            self.assertEqual({path.name for path in profile_dir.iterdir() if path.is_file()}, expected_names)

    def test_both_standalone_datasets_equal_canonical_bytes(self):
        canonical = DATA_PATH.read_bytes()
        for profile in EXPECTED_URLS:
            self.assertEqual((DIST / profile / "public_opportunities.json").read_bytes(), canonical)
        self.assertEqual(
            sha256_path(DIST / "primary" / "public_opportunities.json"),
            sha256_path(DIST / "mirror" / "public_opportunities.json"),
        )

    def test_embedded_json_exactly_matches_canonical_object(self):
        for profile in EXPECTED_URLS:
            self.assertEqual(self.facts(profile).embedded_data, self.data)

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
        expected_ids = {"card-" + record["id"] for record in self.data["opportunities"]}
        parsed = {}
        for profile in EXPECTED_URLS:
            facts = self.facts(profile)
            article_ids = {item["id"] for item in facts.attrs_by_tag["article"]}
            self.assertEqual(article_ids, expected_ids)
            parsed[profile] = facts.embedded_data["opportunities"]
        self.assertEqual(parsed["primary"], parsed["mirror"])

    def test_scrubbed_provenance_manifests(self):
        for profile in EXPECTED_URLS:
            profile_dir = DIST / profile
            manifest = load_json(profile_dir / "provenance.json")
            self.assertEqual(manifest["manifest_version"], "1.0.0")
            self.assertEqual(manifest["snapshot_date"], self.data["page_date"])
            self.assertEqual(manifest["profile"]["id"], profile)
            self.assertEqual(manifest["profile"]["canonical_url"], EXPECTED_URLS[profile])
            self.assertEqual(manifest["canonical_data"]["logical_name"], "data/opportunities.json")
            self.assertEqual(manifest["canonical_data"]["sha256"], sha256_path(DATA_PATH))
            self.assertEqual(manifest["canonical_data"]["record_count"], 32)
            self.assertEqual(manifest["canonical_data"]["status_counts"],
                             {"open": 22, "upcoming": 2, "closed": 8})
            for logical_name, digest in manifest["build_inputs"].items():
                self.assertFalse(Path(logical_name).is_absolute())
                self.assertEqual(digest, sha256_path(ROOT / logical_name))
            for name, digest in manifest["outputs"].items():
                self.assertEqual(digest, sha256_path(profile_dir / name))
            serialized = json.dumps(manifest)
            self.assertNotIn("/" + "Users" + "/", serialized)
            self.assertNotIn("generated_at", serialized)

    def test_rebuild_is_byte_deterministic(self):
        before = tree_hashes(DIST)
        result = subprocess.run(
            [sys.executable, str(ROOT / "src" / "generate.py")],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertEqual(tree_hashes(DIST), before)


if __name__ == "__main__":
    unittest.main()
