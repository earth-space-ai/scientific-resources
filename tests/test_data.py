from __future__ import annotations

import json
import re
import sys
import unittest
from collections import Counter
from datetime import date, datetime
from urllib.parse import urlsplit

from helpers import DATA_PATH, FUNDING_PULSE_SCHEMA_PATH, HISTORY, ROOT, SCHEMA_PATH, load_json, sha256_path

sys.path.insert(0, str(DATA_PATH.parents[1] / "src"))
import generate  # noqa: E402

EXPECTED_ORIGINAL_SHA256 = "a5af1ed92d34f47ffbda372e0d028940b7e5aaa485e4a52486dc34f4388dbc64"
ORIGINAL_ID = "2026-07-28-a5af1ed92d34"


def declared_counts(records):
    observed = Counter(record["status"] for record in records)
    return {"total": len(records), "open": observed["open"], "upcoming": observed["upcoming"], "closed": observed["closed"]}


def refresh(data):
    generate.refresh_derived_counts(data)


class SchemaAssertionError(AssertionError):
    pass


def _resolve_ref(root_schema, reference):
    if not reference.startswith("#/"):
        raise SchemaAssertionError(f"unsupported reference: {reference}")
    node = root_schema
    for part in reference[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _type_matches(value, expected):
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return checks[expected](value)


def assert_schema(value, schema, root_schema, path="$ "):
    if "$ref" in schema:
        return assert_schema(value, _resolve_ref(root_schema, schema["$ref"]), root_schema, path)
    if "anyOf" in schema:
        errors = []
        for option in schema["anyOf"]:
            try:
                assert_schema(value, option, root_schema, path)
                return
            except (AssertionError, ValueError) as exc:
                errors.append(str(exc))
        raise SchemaAssertionError(f"{path} did not match any allowed schema: {errors}")
    if "const" in schema and value != schema["const"]:
        raise SchemaAssertionError(f"{path} did not equal its constant")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaAssertionError(f"{path} was outside its enum")
    expected_type = schema.get("type")
    if expected_type and not _type_matches(value, expected_type):
        raise SchemaAssertionError(f"{path} expected {expected_type}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = set(schema.get("required", []))
        missing = required - set(value)
        if missing:
            raise SchemaAssertionError(f"{path} missing fields: {sorted(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise SchemaAssertionError(f"{path} has unexpected fields: {sorted(extra)}")
        for key, item in value.items():
            if key in properties:
                assert_schema(item, properties[key], root_schema, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaAssertionError(f"{path} has too few items")
        if schema.get("uniqueItems"):
            frozen = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            if len(frozen) != len(set(frozen)):
                raise SchemaAssertionError(f"{path} contains duplicates")
        if "items" in schema:
            for index, item in enumerate(value):
                assert_schema(item, schema["items"], root_schema, f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaAssertionError(f"{path} is too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise SchemaAssertionError(f"{path} did not match its pattern")
        if schema.get("format") == "date":
            date.fromisoformat(value)
        elif schema.get("format") == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif schema.get("format") == "uri":
            parsed = urlsplit(value)
            if not parsed.scheme or not parsed.netloc:
                raise SchemaAssertionError(f"{path} is not an absolute URI")
    elif isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", value):
            raise SchemaAssertionError(f"{path} is below its minimum")


class PublicDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json(DATA_PATH)
        cls.schema = load_json(SCHEMA_PATH)
        cls.records = cls.data["opportunities"]
        cls.history_index = load_json(HISTORY / "index.json")
        cls.funding_pulse_schema = load_json(FUNDING_PULSE_SCHEMA_PATH)
        cls.current_snapshot_id = cls.history_index["current_snapshot_id"]
        cls.current_summary = next(
            item for item in cls.history_index["snapshots"] if item["snapshot_id"] == cls.current_snapshot_id
        )
        cls.current_snapshot_path = HISTORY / "snapshots" / cls.current_snapshot_id / "public_opportunities.json"
        cls.original = load_json(HISTORY / "snapshots" / ORIGINAL_ID / "public_opportunities.json")

    def test_public_schema_document_and_full_validation(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["type"], "object")
        self.assertFalse(self.schema["additionalProperties"])
        assert_schema(self.data, self.schema, self.schema)

    def test_one_canonical_opportunity_data_file(self):
        candidates = sorted(DATA_PATH.parent.glob("*opportunit*.json"))
        self.assertEqual(candidates, [DATA_PATH])

    def test_reviewed_snapshot_is_byte_preserved(self):
        self.assertEqual(sha256_path(DATA_PATH), self.current_summary["canonical_data_sha256"])
        self.assertEqual(DATA_PATH.read_bytes(), self.current_snapshot_path.read_bytes())
        self.assertEqual(self.data["page_date"], self.current_summary["page_date"])
        self.assertEqual(self.data["page_timezone"], "America/Los_Angeles")
        self.assertEqual(self.data["verified_at"], self.current_summary["verified_at"])
        self.assertEqual(
            sha256_path(DATA_PATH.parent / "history" / "snapshots" / ORIGINAL_ID / "public_opportunities.json"),
            EXPECTED_ORIGINAL_SHA256,
        )

    def test_every_source_git_claim_reproduces_canonical_snapshot_bytes(self):
        for item in self.history_index["snapshots"]:
            if item["source"]["kind"] != "source-git":
                continue
            with self.subTest(snapshot_id=item["snapshot_id"]):
                source_bytes = generate.git_show_file_bytes(
                    item["source"]["commit"],
                    item["source"]["path"],
                    repo_root=ROOT,
                )
                self.assertEqual(generate.sha256_bytes(source_bytes), item["canonical_data_sha256"])

    def test_current_funding_pulse_provenance_is_schema_valid_and_cycle_free(self):
        pulse_path = HISTORY / "snapshots" / self.current_snapshot_id / "funding_pulse.json"
        pulse = load_json(pulse_path)
        assert_schema(pulse, self.funding_pulse_schema, self.funding_pulse_schema)
        provenance = pulse["provenance"]
        manifest_path = HISTORY / "snapshots" / self.current_snapshot_id / "change_manifest.json"
        self.assertEqual(provenance["canonical_data_sha256"], self.current_summary["canonical_data_sha256"])
        self.assertEqual(provenance["change_manifest_sha256"], sha256_path(manifest_path))
        self.assertEqual(provenance["change_manifest_path"], f"data/history/snapshots/{self.current_snapshot_id}/change_manifest.json")
        self.assertEqual(provenance["source_git"]["commit"], "cdba70e30101914386650c166cd63e8dd4ad7c2f")
        self.assertNotIn("funding_pulse_sha256", provenance)

    def test_record_and_status_counts(self):
        observed = Counter(record["status"] for record in self.records)
        self.assertEqual(self.data["counts"], declared_counts(self.records))
        self.assertEqual(self.current_summary["record_count"], len(self.records))
        self.assertEqual(self.current_summary["status_counts"], generate.status_counts(self.records))
        self.assertEqual(observed, Counter(self.current_summary["status_counts"]))

    def test_group_status_matrix_uses_allowed_groups_and_statuses(self):
        seen_groups = set()
        for group in generate.GROUP_LABELS:
            observed = Counter(record["status"] for record in self.records if record["group"] == group)
            self.assertEqual(sum(observed.values()), sum(1 for record in self.records if record["group"] == group))
            self.assertTrue(set(observed) <= set(generate.STATUS_LABELS))
            if observed:
                seen_groups.add(group)
        self.assertEqual(seen_groups, set(generate.GROUP_LABELS))

    def test_ids_are_unique_stable_slugs(self):
        ids = [record["id"] for record in self.records]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) for item in ids))

    def test_deadline_and_closing_soon_facts(self):
        self.assertTrue(all(record["deadline_kind"] in {"rolling", "closed", "fixed", "tbd"} for record in self.records))
        self.assertTrue(all(record["status"] == "closed" for record in self.records if record["deadline_kind"] == "closed"))
        for record in self.records:
            if record["next_deadline"]:
                date.fromisoformat(record["next_deadline"])
        soon = [record for record in self.records if record["closing_soon"]]
        self.assertTrue(all(record["status"] == "open" for record in soon))
        self.assertTrue(all(record["next_deadline"] for record in soon))

    def test_application_and_official_urls(self):
        application_records = [record for record in self.records if record["application_url"]]
        self.assertTrue(all(record["status"] == "open" and record["apply_label"] for record in application_records))
        for record in self.records:
            self.assertEqual(bool(record["application_url"]), bool(record["apply_label"]))
            urls = list(record["official_source_urls"])
            if record["application_url"]:
                urls.append(record["application_url"])
            self.assertGreaterEqual(len(record["official_source_urls"]), 1)
            self.assertEqual(len(record["official_source_urls"]), len(set(record["official_source_urls"])))
            for url in urls:
                parsed = urlsplit(url)
                self.assertEqual(parsed.scheme, "https")
                self.assertTrue(parsed.netloc)
                self.assertIsNone(parsed.username)
                self.assertIsNone(parsed.password)

    def test_status_and_endpoint_claims_remain_separate(self):
        definitions = self.data["methodology"]["status_definitions"]
        self.assertEqual(set(definitions), {"open", "upcoming", "closed", "stale-endpoint"})
        self.assertTrue(all(record["endpoint_note"] for record in self.records))
        self.assertTrue(all(record["verified_date"] == self.data["page_date"] for record in self.records))

    def test_lifecycle_invariants(self):
        original_ids = {record["id"] for record in self.original["opportunities"]}
        for record in self.records:
            if record["id"] in original_ids:
                self.assertEqual(record["first_seen"], "2026-07-28")
            self.assertLessEqual(date.fromisoformat(record["first_seen"]), date.fromisoformat(record["last_verified"]))
            self.assertEqual(record["last_verified"], record["verified_date"])
            if record["retired_at"] is None:
                self.assertIsNone(record["retirement_reason"])
            else:
                self.assertEqual(record["status"], "closed")
                self.assertIsInstance(record["retirement_reason"], str)
            self.assertIsNone(record["superseded_by"])
            self.assertIsNone(record["reactivated_at"])

    def test_current_validator_allows_legitimate_added_records_and_status_counts(self):
        future = json.loads(json.dumps(self.data))
        new_record = json.loads(json.dumps(future["opportunities"][0]))
        new_record["id"] = "example-new-resource-2026"
        new_record["program"] = "Example New Resource 2026"
        new_record["first_seen"] = future["page_date"]
        future["opportunities"].append(new_record)
        refresh(future)
        generate.validate_public_data(future)

        future["counts"]["open"] = max(0, future["counts"]["open"] - 1)
        with self.assertRaisesRegex(ValueError, "declared and observed status counts"):
            generate.validate_public_data(future)

    def test_relevance_and_view_counts_are_derived(self):
        self.assertEqual(self.data["view_policy"]["default_view"], "curated")
        self.assertEqual(self.data["view_counts"], generate.view_counts(self.records))
        self.assertEqual(self.data["relevance_counts"], generate.relevance_counts(self.records))
        self.assertFalse(any(record["relevance"]["classification"] == "unknown" for record in self.records))
        unrelated = [record for record in self.records if record["relevance"]["classification"] == "unrelated"]
        self.assertEqual(len(unrelated), 16)
        reasons = {record["relevance"]["public_reason"] for record in unrelated}
        self.assertEqual(len(reasons), 16)
        self.assertFalse(any("Jason" in reason for reason in reasons))

    def test_resource_source_refs_are_official_and_amount_text_is_display_only(self):
        original = generate.build_funding_pulse(self.data)
        mutated = json.loads(json.dumps(self.data))
        mutated["opportunities"][0]["amount"] = "Display text changed; structured measures unchanged."
        changed = generate.build_funding_pulse(mutated)
        self.assertEqual(original["program_pools"], changed["program_pools"])
        for record in self.records:
            official = set(record["official_source_urls"])
            for source_ref in record["resources"]["source_refs"]:
                self.assertIn(source_ref["url"], official)
            for measure in record["resources"]["measures"]:
                for source_ref in measure["source_refs"]:
                    self.assertIn(source_ref["url"], official)

    def test_every_resource_warning_has_public_label(self):
        warnings = {
            warning
            for record in self.records
            for measure in record["resources"]["measures"]
            for warning in measure["warnings"]
        }
        self.assertEqual(warnings - set(generate.WARNING_LABELS), set())
        for warning in warnings:
            label = generate.WARNING_LABELS[warning]
            self.assertNotIn("_", label)
            self.assertTrue(label.endswith("."))

    def test_provider_specific_credits_do_not_share_rollup_rows(self):
        pulse = generate.build_funding_pulse(self.data)
        labels = [entry["label"] for entry in pulse["credits"]]
        self.assertIn("provider:DigitalOcean|resource:cloud-credit|basis:annual-per-project|unit:DigitalOcean credits USD", labels)
        self.assertIn("provider:Microsoft Azure|resource:cloud-credit|basis:annual-per-organization|unit:Azure credits USD", labels)
        self.assertIn("provider:AWS TechAction|resource:cloud-credit|basis:per-project-ceiling|unit:AWS Promotional Credit USD", labels)
        self.assertFalse(any(entry["amount"]["maximum_sum"] == 22000 for entry in pulse["credits"]))

        broken = json.loads(json.dumps(self.data))
        by_id = {record["id"]: record for record in broken["opportunities"]}
        do_measure = by_id["digitalocean-open-source-sponsorship"]["resources"]["measures"][0]
        azure_measure = by_id["microsoft-nonprofits-azure-grant"]["resources"]["measures"][0]
        azure_measure["aggregation"]["rollup_group_key"] = do_measure["aggregation"]["rollup_group_key"]
        with self.assertRaisesRegex(ValueError, "provider/platform-specific credits"):
            generate.validate_public_data(broken)

    def test_psf_alternative_caps_remain_separate(self):
        pulse = generate.build_funding_pulse(self.data)
        psf_rows = [
            entry for entry in pulse["per_award_ranges"]
            if entry["label"].startswith("provider:Python Software Foundation")
        ]
        self.assertEqual({entry["amount"]["maximum_sum"] for entry in psf_rows}, {1500.0, 2000.0})
        self.assertFalse(any(entry["amount"]["maximum_sum"] == 3500 for entry in psf_rows))


if __name__ == "__main__":
    unittest.main()
