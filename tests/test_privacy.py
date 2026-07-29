from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from helpers import DATA_PATH, ROOT, load_json

TEXT_NAMES = {"LICENSE", ".gitignore"}
TEXT_SUFFIXES = {".json", ".py", ".html", ".md", ".yml", ".yaml"}
EXPECTED_PUBLIC_FIELDS = {
    "id",
    "program",
    "provider",
    "group",
    "group_label",
    "status",
    "amount",
    "deadline",
    "next_deadline",
    "deadline_kind",
    "closing_soon",
    "eligibility",
    "endpoint_note",
    "application_url",
    "apply_label",
    "official_source_urls",
    "verified_at",
    "verified_date",
}


def publishable_text_files():
    ignored_parts = {"__" + "pycache__", ".git"}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in ignored_parts for part in path.parts):
            continue
        if path.name in TEXT_NAMES or path.suffix in TEXT_SUFFIXES:
            yield path


def marker(parts):
    return "".join(parts)


class PrivacyAndSecretCategoryTests(unittest.TestCase):
    def test_canonical_records_have_only_the_public_field_allowlist(self):
        records = load_json(DATA_PATH)["opportunities"]
        self.assertEqual(len(records), 32)
        for record in records:
            self.assertEqual(set(record), EXPECTED_PUBLIC_FIELDS)

    def test_no_local_machine_or_upstream_workspace_paths(self):
        local_root = marker(["/", "Users", "/"])
        upstream_labels = [marker(["base", "line_"]), marker(["project", "_digest"])]
        for path in publishable_text_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(local_root, text, msg=str(path.relative_to(ROOT)))
            for label in upstream_labels:
                self.assertNotIn(label, text, msg=str(path.relative_to(ROOT)))

    def test_no_private_key_blocks_or_known_service_value_formats(self):
        block_marker = marker(["-----BEGIN ", "PRIVATE", " KEY-----"])
        value_patterns = [
            re.compile(marker([r"gh", r"[pousr]", r"_[A-Za-z0-9]{20,}"])),
            re.compile(marker([r"AK", r"IA[0-9A-Z]{16}"])),
            re.compile(marker([r"AI", r"za[0-9A-Za-z_-]{30,}"])),
            re.compile(marker([r"xox", r"[abprs]-[0-9A-Za-z-]{10,}"])),
            re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
        ]
        for path in publishable_text_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(block_marker, text, msg=str(path.relative_to(ROOT)))
            for pattern in value_patterns:
                self.assertIsNone(pattern.search(text), msg=f"{path.relative_to(ROOT)} matched {pattern.pattern}")

    def test_no_sensitive_environment_or_key_material_filenames(self):
        risky_names = {
            marker([".e", "nv"]),
            marker(["id_", "rsa"]),
            marker(["id_", "ed25519"]),
            marker(["creden", "tials"]),
            marker(["key", "chain"]),
        }
        for path in ROOT.rglob("*"):
            if path.is_file():
                lowered = path.name.lower()
                self.assertFalse(any(name in lowered for name in risky_names), msg=str(path.relative_to(ROOT)))

    def test_json_files_do_not_contain_unexpected_high_risk_categories(self):
        prohibited_keys = {
            marker(["api", "_key"]),
            marker(["access", "_token"]),
            marker(["refresh", "_token"]),
            marker(["client", "_secret"]),
            marker(["private", "_key"]),
            marker(["pass", "word"]),
        }

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key.lower(), prohibited_keys)
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for path in publishable_text_files():
            if path.suffix == ".json":
                walk(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
