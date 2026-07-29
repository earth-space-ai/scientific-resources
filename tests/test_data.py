from __future__ import annotations

import json
import re
import unittest
from collections import Counter
from datetime import date, datetime
from urllib.parse import urlsplit

from helpers import DATA_PATH, SCHEMA_PATH, load_json, sha256_path

EXPECTED_DATA_SHA256 = "a5af1ed92d34f47ffbda372e0d028940b7e5aaa485e4a52486dc34f4388dbc64"
EXPECTED_STATUS_COUNTS = {"total": 32, "open": 22, "upcoming": 2, "closed": 8}
EXPECTED_GROUP_STATUS = {
    "credits": Counter({"open": 7, "closed": 2}),
    "hpc": Counter({"open": 10, "closed": 3, "upcoming": 1}),
    "grants": Counter({"open": 5, "closed": 3, "upcoming": 1}),
}
EXPECTED_DEADLINES = [
    "2026-07-31",
    "2026-08-02",
    "2026-08-07",
    "2026-09-01",
    "2026-10-29",
    "2026-11-04",
]


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

    def test_public_schema_document_and_full_validation(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["type"], "object")
        self.assertFalse(self.schema["additionalProperties"])
        assert_schema(self.data, self.schema, self.schema)

    def test_one_canonical_opportunity_data_file(self):
        candidates = sorted(DATA_PATH.parent.glob("*opportunit*.json"))
        self.assertEqual(candidates, [DATA_PATH])

    def test_reviewed_snapshot_is_byte_preserved(self):
        self.assertEqual(sha256_path(DATA_PATH), EXPECTED_DATA_SHA256)
        self.assertEqual(self.data["page_date"], "2026-07-28")
        self.assertEqual(self.data["page_timezone"], "America/Los_Angeles")
        self.assertEqual(self.data["verified_at"], "2026-07-29T04:27:05Z")

    def test_record_and_status_counts(self):
        self.assertEqual(len(self.records), 32)
        observed = Counter(record["status"] for record in self.records)
        self.assertEqual(self.data["counts"], EXPECTED_STATUS_COUNTS)
        self.assertEqual(observed, Counter({"open": 22, "closed": 8, "upcoming": 2}))

    def test_group_status_matrix(self):
        for group, expected in EXPECTED_GROUP_STATUS.items():
            observed = Counter(record["status"] for record in self.records if record["group"] == group)
            self.assertEqual(observed, expected)

    def test_ids_are_unique_stable_slugs(self):
        ids = [record["id"] for record in self.records]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) for item in ids))

    def test_deadline_and_closing_soon_facts(self):
        self.assertEqual(Counter(record["deadline_kind"] for record in self.records),
                         Counter({"rolling": 15, "closed": 8, "fixed": 7, "tbd": 2}))
        self.assertEqual(sorted(record["next_deadline"] for record in self.records if record["next_deadline"]),
                         EXPECTED_DEADLINES)
        soon = [record for record in self.records if record["closing_soon"]]
        self.assertEqual(len(soon), 3)
        self.assertTrue(all(record["status"] == "open" for record in soon))

    def test_application_and_official_urls(self):
        application_records = [record for record in self.records if record["application_url"]]
        self.assertEqual(len(application_records), 22)
        self.assertTrue(all(record["status"] == "open" and record["apply_label"] for record in application_records))
        self.assertEqual(sum(len(record["official_source_urls"]) for record in self.records), 77)
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


if __name__ == "__main__":
    unittest.main()
