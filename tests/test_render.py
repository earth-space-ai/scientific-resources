from __future__ import annotations

import html
import re
import unittest
from urllib.parse import urlsplit

from helpers import DATA_PATH, DIST, PROFILES_PATH, HTMLFacts, load_json


class RenderContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json(DATA_PATH)
        cls.profiles = load_json(PROFILES_PATH)["profiles"]
        cls.allowed_external = {
            url
            for record in cls.data["opportunities"]
            for url in record["official_source_urls"] + ([record["application_url"]] if record["application_url"] else [])
        }
        for profile in cls.profiles.values():
            cls.allowed_external.add(profile["canonical_url"])
            cls.allowed_external.add(profile["alternate_url"])

    def parsed(self, profile):
        text = (DIST / profile / "index.html").read_text(encoding="utf-8")
        parser = HTMLFacts()
        parser.feed(text)
        return text, parser

    def test_semantic_self_contained_document(self):
        for profile in ("primary", "mirror"):
            text, facts = self.parsed(profile)
            self.assertTrue(text.startswith("<!doctype html>"))
            self.assertEqual(facts.attrs_by_tag["html"][0].get("lang"), "en")
            self.assertEqual(facts.start_counts.get("main"), 1)
            self.assertEqual(facts.start_counts.get("h1"), 1)
            self.assertGreaterEqual(facts.start_counts.get("h2", 0), 5)
            self.assertEqual(facts.start_counts.get("article"), 32)
            self.assertGreaterEqual(facts.start_counts.get("nav", 0), 1)
            self.assertGreaterEqual(facts.start_counts.get("details", 0), 33)
            self.assertIn('href="#main"', text)
            self.assertNotRegex(text, r"\{\{[A-Z0-9_]+\}\}")

    def test_all_records_are_pre_rendered_without_javascript(self):
        for profile in ("primary", "mirror"):
            text, facts = self.parsed(profile)
            self.assertEqual(facts.start_counts.get("article"), len(self.data["opportunities"]))
            for record in self.data["opportunities"]:
                self.assertIn('id="card-' + record["id"] + '"', text)
                self.assertIn(html.escape(record["program"], quote=True), text)

    def test_card_status_and_application_link_counts(self):
        for profile in ("primary", "mirror"):
            text, _ = self.parsed(profile)
            self.assertEqual(len(re.findall(r'<article class="card card-open"', text)), 22)
            self.assertEqual(len(re.findall(r'<article class="card card-upcoming"', text)), 2)
            self.assertEqual(len(re.findall(r'<article class="card card-closed"', text)), 8)
            self.assertEqual(len(re.findall(r'class="button apply-link"', text)), 22)
            self.assertEqual(len(re.findall(r'badge badge-soon', text)), 3)

    def test_no_external_runtime_dependencies(self):
        for profile in ("primary", "mirror"):
            text, facts = self.parsed(profile)
            for script in facts.attrs_by_tag.get("script", []):
                self.assertNotIn("src", script)
            for link in facts.attrs_by_tag.get("link", []):
                rel = set(link.get("rel", "").split())
                self.assertTrue(rel <= {"canonical", "alternate"})
            for image in facts.attrs_by_tag.get("img", []):
                self.assertFalse(urlsplit(image.get("src", "")).scheme)
            style_blocks = re.findall(r"<style>(.*?)</style>", text, flags=re.DOTALL | re.IGNORECASE)
            self.assertEqual(len(style_blocks), 1)
            self.assertNotRegex(style_blocks[0], r"(?i)@import|url\s*\(\s*['\"]?https?://")
            self.assertNotRegex(text, r"(?i)(analytics|tagmanager|doubleclick)\.")

    def test_every_external_href_is_reviewed_public_metadata_or_data(self):
        for profile in ("primary", "mirror"):
            _, facts = self.parsed(profile)
            for anchor in facts.attrs_by_tag.get("a", []):
                href = anchor.get("href", "")
                parsed = urlsplit(href)
                if parsed.scheme:
                    self.assertEqual(parsed.scheme, "https")
                    self.assertIn(href, self.allowed_external)
                    self.assertIsNone(parsed.username)
                    self.assertIsNone(parsed.password)

    def test_data_and_provenance_links_are_exact_root_relative_paths(self):
        data_href = 'href="/scientific-resources/public_opportunities.json"'
        provenance_href = 'href="/scientific-resources/provenance.json"'
        for profile in ("primary", "mirror"):
            text, _ = self.parsed(profile)
            self.assertEqual(text.count(data_href), 2)
            self.assertEqual(text.count(provenance_href), 1)
            self.assertNotIn('href="public_opportunities.json"', text)
            self.assertNotIn('href="./public_opportunities.json"', text)
            self.assertNotIn('href="provenance.json"', text)
            self.assertNotIn('href="./provenance.json"', text)

    def test_accessibility_and_responsive_contract_markers(self):
        for profile in ("primary", "mirror"):
            text, _ = self.parsed(profile)
            self.assertIn('aria-live="polite"', text)
            self.assertIn('role="status"', text)
            self.assertIn('aria-label="Snapshot counts"', text)
            self.assertIn("--tap: 44px", text)
            self.assertIn("prefers-reduced-motion: reduce", text)
            self.assertIn("@media print", text)
            self.assertIn("@media (max-width: 700px)", text)


if __name__ == "__main__":
    unittest.main()
