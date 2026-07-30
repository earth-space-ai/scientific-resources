from __future__ import annotations

import html
import re
import unittest
from collections import Counter
from urllib.parse import urlsplit

from helpers import DATA_PATH, DIST, PROFILES_PATH, HTMLFacts, load_json


SOURCE_REPOSITORY_URL = "https://github.com/earth-space-ai/scientific-resources"


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
            cls.allowed_external.add(profile["source_repository_url"])

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
            self.assertEqual(facts.start_counts.get("article"), len(self.data["opportunities"]))
            self.assertGreaterEqual(facts.start_counts.get("nav", 0), 1)
            self.assertGreaterEqual(facts.start_counts.get("details", 0), len(self.data["opportunities"]) + 1)
            self.assertIn('role="tablist"', text)
            self.assertIn('id="records-root" role="tabpanel" aria-labelledby="tab-curated"', text)
            self.assertIn('aria-controls="records-root"', text)
            self.assertIn('recordsRoot.setAttribute("aria-labelledby"', text)
            self.assertIn("Curated for Jason", text)
            self.assertIn("All Opportunities", text)
            self.assertIn('button[role="tab"][aria-selected="true"]', text)
            self.assertIn('button[aria-disabled="true"]', text)
            self.assertIn('button.removeAttribute("aria-disabled")', text)
            self.assertIn("Any status", text)
            self.assertIn("Funding Pulse measures officially quantified resources", text)
            self.assertIn("Partially quantified current records", text)
            self.assertIn("No comparable Funding Pulse baseline for this legacy snapshot", text)
            self.assertIn("Legacy 1.0.0 snapshot: full-universe relevance classification and All Opportunities are unavailable", text)
            self.assertIn("Funding Pulse unavailable for this legacy snapshot", text)
            pulse_section = text.split('<section class="funding-pulse"', 1)[1].split('<div id="records-root"', 1)[0]
            for public_term in (
                "Round budget",
                "Conference grant cap",
                "Workshop grant cap",
                "Annual credit range",
                "Typical QPU-hours allocation",
                "USD 90,000 round budget",
                "Conference grants up to USD 2,000",
                "Workshop grants up to USD 1,500",
                "Up to USD 5,000 AWS Promotional Credit",
                "one-year sponsorship",
                "Provider-specific credit; not fungible with other providers.",
                "U.S. sponsor current record",
                "Per-award amount",
            ):
                self.assertIn(public_term, pulse_section)
            for raw_term in (
                "program-pool-usd",
                "conference-cap-usd",
                "annual-credit-usd",
                "typical-qpu-hours",
                "per_award_ceiling_not_program_pool",
                "provider_specific_credit_not_fungible",
                "Up to 5000 AWS Promotional Credit USD",
                "cash|program_pool",
                "provider:DigitalOcean",
            ):
                self.assertNotIn(raw_term, pulse_section)
            self.assertNotIn("pulse-table", text)
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
        status_counts = Counter(record["status"] for record in self.data["opportunities"])
        application_count = sum(1 for record in self.data["opportunities"] if record["application_url"])
        closing_soon_count = sum(1 for record in self.data["opportunities"] if record["closing_soon"])
        for profile in ("primary", "mirror"):
            text, _ = self.parsed(profile)
            self.assertEqual(len(re.findall(r'<article class="card card-open(?: is-hidden)?"', text)), status_counts["open"])
            self.assertEqual(len(re.findall(r'<article class="card card-upcoming(?: is-hidden)?"', text)), status_counts["upcoming"])
            self.assertEqual(len(re.findall(r'<article class="card card-closed(?: is-hidden)?"', text)), status_counts["closed"])
            self.assertEqual(len(re.findall(r'class="button apply-link"', text)), application_count)
            self.assertEqual(len(re.findall(r'<span class="badge badge-soon"', text)), closing_soon_count)

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

    def test_root_relative_time_machine_fetch_paths(self):
        for profile in ("primary", "mirror"):
            text, _ = self.parsed(profile)
            self.assertIn('fetch("/scientific-resources/snapshots/index.json"', text)
            self.assertIn('item.data_url', text)
            self.assertIn('item.change_manifest_url', text)
            self.assertIn('item.funding_pulse_url', text)
            self.assertIn('renderPulseUnavailable()', text)
            self.assertNotIn('"./snapshots/', text)
            self.assertNotIn('"snapshots/', text)

    def test_time_machine_updates_visible_snapshot_summary_surfaces(self):
        expected_ids = (
            "page-open-count",
            "summary-total-count",
            "summary-open-count",
            "summary-upcoming-count",
            "summary-closed-count",
            "snapshot-date",
            "snapshot-timezone",
        )
        expected_assignments = (
            "pageOpenCount.textContent = scopedCounts.open;",
            "summaryTotalCount.textContent = scopedCounts.total;",
            "summaryOpenCount.textContent = scopedCounts.open;",
            "summaryUpcomingCount.textContent = scopedCounts.upcoming;",
            "summaryClosedCount.textContent = scopedCounts.closed;",
            "snapshotDate.textContent = data.page_date;",
            'snapshotDate.setAttribute("datetime", data.page_date);',
            "snapshotTimezone.textContent = data.page_timezone;",
        )
        for profile in ("primary", "mirror"):
            text, _ = self.parsed(profile)
            for element_id in expected_ids:
                self.assertEqual(text.count(f'id="{element_id}"'), 1)
            for assignment in expected_assignments:
                self.assertIn(assignment, text)

    def test_visible_source_repository_link_in_both_profiles(self):
        for profile in ("primary", "mirror"):
            text, facts = self.parsed(profile)
            self.assertEqual(self.profiles[profile]["source_repository_url"], SOURCE_REPOSITORY_URL)
            self.assertEqual(text.count("View source on GitHub"), 1)
            self.assertIn(
                f'<a href="{SOURCE_REPOSITORY_URL}" rel="noopener noreferrer">'
                'View source on GitHub<span aria-hidden="true"> ↗</span></a>',
                text,
            )
            repository_links = [
                anchor
                for anchor in facts.attrs_by_tag.get("a", [])
                if anchor.get("href") == SOURCE_REPOSITORY_URL
            ]
            self.assertEqual(len(repository_links), 1)
            self.assertEqual(set(repository_links[0].get("rel", "").split()), {"noopener", "noreferrer"})

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
