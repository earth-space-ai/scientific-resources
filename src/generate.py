#!/usr/bin/env python3
"""Build deterministic primary and mirror static sites from one public dataset.

The generator uses only the Python standard library, reads public repository
inputs, and writes host-profile-specific artifacts. It does not contact remote
services or infer newer facts than the reviewed snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

BUILD_SPEC_VERSION = "1.1.0"
SNAPSHOT_INDEX_VERSION = "1.1.0"
CHANGE_MANIFEST_VERSION = "1.1.0"
FUNDING_PULSE_VERSION = "1.0.0"
EXPECTED_PROFILE_URLS = {
    "primary": "https://earth-space-ai.org/scientific-resources",
    "mirror": "https://huangzesen.github.io/scientific-resources/",
}
EXPECTED_SOURCE_REPOSITORY_URL = "https://github.com/earth-space-ai/scientific-resources"
GROUP_LABELS = {
    "credits": "API & cloud research credits",
    "hpc": "HPC & GPU allocations",
    "grants": "AI-for-science & open-science grants",
}
STATUS_LABELS = {"open": "Open", "upcoming": "Upcoming", "closed": "Archived"}
RELEVANCE_CLASSES = {"direct", "adjacent", "unrelated"}
CURATED_RELEVANCE_CLASSES = {"direct", "adjacent"}
SPONSOR_SECTORS = {
    "us_federal",
    "us_state_local",
    "foundation_nonprofit",
    "industry",
    "university_consortium",
    "international_public",
    "other",
}
ACCOUNTING_BUCKETS = {
    "program_pool",
    "per_award",
    "cash",
    "credit",
    "compute",
    "facility",
    "prize",
    "fellowship",
    "other_in_kind",
}
RESOURCE_KINDS = {
    "cash",
    "cloud_credit",
    "api_credit",
    "compute_access",
    "facility_access",
    "fellowship",
    "prize",
    "other_in_kind",
}
ROLLUP_POLICIES = {"self", "parent_only", "child_only", "exclude"}
QUANTIFICATION_STATUSES = {"quantified", "partially_quantified", "unquantified", "individualized"}
ARCHIVE_LEAD_DAYS = 15
CLOSING_SOON_DAYS = 10
RECORD_FIELDS = {
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
    "first_seen",
    "last_verified",
    "retired_at",
    "retirement_reason",
    "superseded_by",
    "reactivated_at",
    "relevance",
    "landscape",
    "resources",
}
LEGACY_RECORD_FIELDS = RECORD_FIELDS - {
    "first_seen",
    "last_verified",
    "retired_at",
    "retirement_reason",
    "superseded_by",
    "reactivated_at",
    "relevance",
    "landscape",
    "resources",
}
RECORD_FIELDS_V1_0 = RECORD_FIELDS - {"relevance", "landscape", "resources"}
OPERATIONAL_LIFECYCLE_FIELDS = {"first_seen", "last_verified", "verified_at", "verified_date"}
LIFECYCLE_TRANSITION_FIELDS = {"retired_at", "retirement_reason", "superseded_by", "reactivated_at"}
HISTORY_ROOT_RELATIVE = "/scientific-resources/snapshots"


def canonical_json_bytes(value: object) -> bytes:
    """Return the repository's stable UTF-8 JSON representation."""
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def git_show_file_bytes(commit: str, path: str, *, repo_root: Path | None = None) -> bytes:
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("source-git commit must be a full 40-character lowercase SHA")
    if path != "data/opportunities.json":
        raise ValueError("source-git path must be data/opportunities.json")
    root = repo_root or project_root()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{path}"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ValueError("could not read source-git object") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(message or "could not read source-git object")
    return result.stdout


def validate_source_custody(summary: dict, *, repo_root: Path | None = None) -> None:
    source = summary["source"]
    if source.get("kind") != "source-git":
        return
    if set(source) != {"kind", "commit", "path"}:
        raise ValueError("source-git custody has an unexpected shape")
    source_bytes = git_show_file_bytes(source["commit"], source["path"], repo_root=repo_root)
    if sha256_bytes(source_bytes) != summary["canonical_data_sha256"]:
        raise ValueError(f"source-git custody hash mismatch: {summary['snapshot_id']}")


def validate_funding_pulse_sidecar(
    *,
    history_dir: Path,
    summary: dict,
    data: dict,
    manifest: dict,
    manifest_bytes: bytes,
    source_repository: str,
) -> None:
    pulse_fields = {"funding_pulse_summary", "funding_pulse_url", "funding_pulse_sha256"}
    present = pulse_fields & set(summary)
    if not present:
        return
    if present != pulse_fields:
        raise ValueError(f"funding pulse index fields are incomplete: {summary['snapshot_id']}")
    snapshot_id = summary["snapshot_id"]
    pulse_path = history_dir / "snapshots" / snapshot_id / "funding_pulse.json"
    if not pulse_path.is_file():
        raise ValueError(f"funding pulse sidecar is missing: {snapshot_id}")
    pulse_bytes = pulse_path.read_bytes()
    pulse_sha = sha256_bytes(pulse_bytes)
    if pulse_sha != summary["funding_pulse_sha256"]:
        raise ValueError(f"funding pulse hash mismatch: {snapshot_id}")
    if summary["funding_pulse_sha256"].encode("ascii") in pulse_bytes:
        raise ValueError(f"funding pulse must not contain its own SHA: {snapshot_id}")
    pulse = json.loads(pulse_bytes.decode("utf-8"))
    if pulse.get("snapshot_id") != snapshot_id:
        raise ValueError(f"funding pulse snapshot mismatch: {snapshot_id}")
    if funding_pulse_summary(pulse) != summary["funding_pulse_summary"]:
        raise ValueError(f"funding pulse summary mismatch: {snapshot_id}")

    provenance = pulse.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"funding pulse provenance is missing: {snapshot_id}")
    expected_manifest_path = f"data/history/snapshots/{snapshot_id}/change_manifest.json"
    expected_manifest_url = f"{HISTORY_ROOT_RELATIVE}/{snapshot_id}/change_manifest.json"
    if provenance.get("canonical_data_sha256") != summary["canonical_data_sha256"]:
        raise ValueError(f"funding pulse provenance data SHA mismatch: {snapshot_id}")
    if provenance.get("change_manifest_sha256") != sha256_bytes(manifest_bytes):
        raise ValueError(f"funding pulse provenance manifest SHA mismatch: {snapshot_id}")
    if provenance.get("change_manifest_path") != expected_manifest_path:
        raise ValueError(f"funding pulse provenance manifest path mismatch: {snapshot_id}")
    if provenance.get("change_manifest_url") != expected_manifest_url:
        raise ValueError(f"funding pulse provenance manifest URL mismatch: {snapshot_id}")
    source_git = provenance.get("source_git")
    if not isinstance(source_git, dict):
        raise ValueError(f"funding pulse provenance source-git is missing: {snapshot_id}")
    if source_git.get("source_repository") != source_repository:
        raise ValueError(f"funding pulse provenance source repository mismatch: {snapshot_id}")
    if source_git.get("commit") != summary["source"].get("commit"):
        raise ValueError(f"funding pulse provenance source commit mismatch: {snapshot_id}")
    if source_git.get("path") != summary["source"].get("path"):
        raise ValueError(f"funding pulse provenance source path mismatch: {snapshot_id}")
    if "funding_pulse_sha256" in json.dumps(provenance, ensure_ascii=False):
        raise ValueError(f"funding pulse provenance must not contain a pulse self-hash field: {snapshot_id}")


def parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO calendar date") from exc


def parse_timestamp(value: str, label: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc


def require_public_https(value: str, label: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} must be a public HTTPS URL without embedded user information")


def record_fingerprint(record: dict) -> str:
    return sha256_bytes(canonical_json_bytes(record))


def stable_id_sha256(records: list[dict]) -> str:
    return sha256_bytes(canonical_json_bytes(sorted(record["id"] for record in records)))


def snapshot_id_for(data_sha: str, page_date: str) -> str:
    return f"{page_date}-{data_sha[:12]}"


def validate_snapshot_id(snapshot_id: str, label: str = "snapshot_id") -> None:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-f0-9]{12}", snapshot_id):
        raise ValueError(f"{label} must be YYYY-MM-DD plus a 12-character SHA prefix")


def status_counts(records: list[dict]) -> dict[str, int]:
    observed = Counter(record.get("status") for record in records)
    return {key: observed.get(key, 0) for key in ("open", "upcoming", "closed")}


def record_is_curated(record: dict) -> bool:
    relevance = record.get("relevance")
    if not isinstance(relevance, dict):
        return True
    return relevance.get("classification") in CURATED_RELEVANCE_CLASSES


def view_records(records: list[dict], view_id: str) -> list[dict]:
    if view_id == "all":
        return list(records)
    if view_id == "curated":
        return [record for record in records if record_is_curated(record)]
    raise ValueError(f"unsupported view: {view_id}")


def view_counts(records: list[dict]) -> dict[str, dict[str, int]]:
    curated = view_records(records, "curated")
    all_counts = {"total": len(records), **status_counts(records)}
    curated_counts = {"total": len(curated), **status_counts(curated)}
    return {"curated": curated_counts, "all": all_counts}


def relevance_counts(records: list[dict]) -> dict[str, int]:
    observed = Counter(record.get("relevance", {}).get("classification", "legacy") for record in records)
    return {key: observed.get(key, 0) for key in ("direct", "adjacent", "unrelated")}


def refresh_derived_counts(data: dict) -> None:
    records = data["opportunities"]
    data["counts"] = {"total": len(records), **status_counts(records)}
    if data.get("schema_version") == "1.1.0":
        data["view_counts"] = view_counts(records)
        data["relevance_counts"] = relevance_counts(records)


def current_records(records: list[dict]) -> list[dict]:
    return [record for record in records if record["status"] in {"open", "upcoming"}]


def measure_amount_values(amount: dict) -> tuple[float, float]:
    exact = amount.get("exact")
    minimum = amount.get("minimum")
    maximum = amount.get("maximum")
    if exact is not None:
        return float(exact), float(exact)
    return (float(minimum or 0), float(maximum or 0))


def pulse_rollup_bucket(measure: dict) -> str | None:
    bucket = measure["accounting_bucket"]
    if bucket in {"program_pool", "per_award", "credit", "compute", "facility", "prize", "fellowship", "other_in_kind"}:
        return bucket
    return None


def build_funding_pulse(
    data: dict,
    manifest: dict | None = None,
    *,
    snapshot_id: str | None = None,
    provenance: dict | None = None,
    baseline_available: bool = False,
    baseline_reason: str | None = "previous_snapshot_has_no_funding_pulse",
) -> dict:
    """Build deterministic pulse data from structured resource facts only."""
    records = data["opportunities"]
    active_records = current_records(records)
    coverage = Counter(record["resources"]["quantification_status"] for record in records)
    active_coverage = Counter(record["resources"]["quantification_status"] for record in active_records)
    base = {
        "schema_version": FUNDING_PULSE_VERSION,
        "snapshot_id": snapshot_id,
        "as_of_date": data["page_date"],
        "scope": {
            "dataset": "officially quantified visible resources in this maintained database snapshot",
            "exhaustiveness_warning": "This is not all funding in existence and not an internet-wide estimate.",
            "current_record_scope": "Only open and upcoming records contribute to current resource buckets.",
            "us_sponsor_scope": "U.S.-only aggregates include records with landscape.sponsor_country=US only.",
        },
        "record_totals": {"total": len(records), **status_counts(records), "current": len(active_records)},
        "view_record_counts": view_counts(records),
        "snapshot_delta": {
            "counts": (manifest or {}).get("counts", {key: 0 for key in ("added", "changed", "retired", "reactivated", "unchanged")}),
            "change_manifest_url": f"{HISTORY_ROOT_RELATIVE}/{snapshot_id}/change_manifest.json" if snapshot_id else None,
            "baseline_available": baseline_available,
            "baseline_reason": None if baseline_available else baseline_reason,
        },
        "provenance": provenance,
        "coverage": {
            "records_total": len(records),
            "current_records": len(active_records),
            "archived_records": len(records) - len(active_records),
            "quantified_records": coverage.get("quantified", 0),
            "partially_quantified_records": coverage.get("partially_quantified", 0),
            "unquantified_records": coverage.get("unquantified", 0),
            "individualized_records": coverage.get("individualized", 0),
            "quantified_current_records": active_coverage.get("quantified", 0),
            "partially_quantified_current_records": active_coverage.get("partially_quantified", 0),
            "unquantified_current_records": active_coverage.get("unquantified", 0),
            "individualized_current_records": active_coverage.get("individualized", 0),
            "unknown_coverage_note": "Unknown and individualized opportunities remain verified records but are excluded from quantified totals.",
        },
        "program_pools": [],
        "per_award_ranges": [],
        "credits": [],
        "compute_facility": [],
        "prizes_fellowships": [],
        "other_in_kind": [],
        "suppressed": [],
        "warnings": [
            "Program pools, per-award ranges, credits, compute/facility allocations, fellowships/prizes, and in-kind support are not interchangeable.",
            "Per-award ceilings are not multiplied by guessed award counts.",
            "Currencies are not converted or summed across currencies without a dated official conversion source.",
        ],
    }
    buckets: dict[tuple[str, str, str, str, str], dict] = {}
    seen_double_count: dict[tuple[str, str], tuple[str, str]] = {}
    for record in active_records:
        sponsor_scope = "us_sponsor" if record["landscape"]["sponsor_country"] == "US" else "all_only"
        for measure in record["resources"].get("measures", []):
            if not measure["aggregation"]["include_in_rollup"]:
                base["suppressed"].append({
                    "record_id": record["id"],
                    "measure_id": measure["measure_id"],
                    "reason": "excluded_by_record_resource_policy",
                })
                continue
            bucket_kind = pulse_rollup_bucket(measure)
            if not bucket_kind:
                continue
            unit = measure["unit"]
            period = measure["period"]
            amount = measure["amount"]
            rollup_key = measure["aggregation"]["rollup_group_key"]
            key = (bucket_kind, rollup_key)
            double_key = (bucket_kind, measure["aggregation"]["double_count_key"])
            if double_key in seen_double_count:
                base["suppressed"].append({
                    "record_id": record["id"],
                    "measure_id": measure["measure_id"],
                    "reason": "duplicate_source_or_umbrella_child",
                    "kept_record_id": seen_double_count[double_key][0],
                    "kept_measure_id": seen_double_count[double_key][1],
                })
                continue
            seen_double_count[double_key] = (record["id"], measure["measure_id"])
            entry = buckets.setdefault(key, {
                "bucket_id": rollup_key.lower().replace("|", "-").replace(":", "-").replace("_", "-").replace(" ", "-"),
                "label": rollup_key,
                "scope": sponsor_scope,
                "accounting_bucket": bucket_kind,
                "resource_kind": measure["resource_kind"],
                "unit": unit,
                "period": period,
                "amount": {"minimum_sum": 0, "maximum_sum": 0},
                "records": [],
                "source_urls": [],
                "aggregation_note": "Aggregated only within identical resource kind, unit, basis bucket, period, and current status.",
            })
            min_value, max_value = measure_amount_values(amount)
            entry["amount"]["minimum_sum"] += min_value
            entry["amount"]["maximum_sum"] += max_value
            entry["records"].append({"id": record["id"], "measure_id": measure["measure_id"]})
            for source in measure["source_refs"]:
                if source["url"] not in entry["source_urls"]:
                    entry["source_urls"].append(source["url"])
    for entry in sorted(buckets.values(), key=lambda item: item["bucket_id"]):
        if entry["accounting_bucket"] == "program_pool":
            base["program_pools"].append(entry)
        elif entry["accounting_bucket"] == "per_award":
            base["per_award_ranges"].append(entry)
        elif entry["accounting_bucket"] == "credit":
            base["credits"].append(entry)
        elif entry["accounting_bucket"] in {"compute", "facility"}:
            base["compute_facility"].append(entry)
        elif entry["accounting_bucket"] in {"prize", "fellowship"}:
            base["prizes_fellowships"].append(entry)
        else:
            base["other_in_kind"].append(entry)
    return base


def funding_pulse_summary(pulse: dict | None) -> dict:
    if not pulse:
        return {"state": "legacy_pulse_unavailable"}
    coverage = pulse["coverage"]
    return {
        "state": "available",
        "program_pool_buckets": len(pulse["program_pools"]),
        "per_award_buckets": len(pulse["per_award_ranges"]),
        "credit_buckets": len(pulse["credits"]),
        "compute_facility_buckets": len(pulse["compute_facility"]),
        "prize_fellowship_buckets": len(pulse["prizes_fellowships"]),
        "quantified_current_records": coverage["quantified_current_records"],
        "unquantified_current_records": coverage["unquantified_current_records"],
    }


def validate_lifecycle(record: dict, label: str) -> None:
    first_seen = parse_date(record["first_seen"], f"{label}.first_seen")
    last_verified = parse_date(record["last_verified"], f"{label}.last_verified")
    if first_seen > last_verified:
        raise ValueError(f"{label} first_seen must not be after last_verified")
    if record["last_verified"] != record["verified_date"]:
        raise ValueError(f"{label} last_verified must match verified_date")

    retired_at = record["retired_at"]
    retirement_reason = record["retirement_reason"]
    retired_date = None
    if retired_at is None:
        if retirement_reason is not None:
            raise ValueError(f"{label} retirement_reason requires retired_at")
    else:
        retired_date = parse_date(retired_at, f"{label}.retired_at")
        if retired_date < first_seen or retired_date > last_verified:
            raise ValueError(f"{label} retired_at must fall between first_seen and last_verified")
        if record["status"] != "closed":
            raise ValueError(f"{label} retired records must remain status=closed")
        if not isinstance(retirement_reason, str) or not retirement_reason.strip():
            raise ValueError(f"{label} retired records require a retirement_reason")

    superseded_by = record["superseded_by"]
    if superseded_by is not None:
        if not isinstance(superseded_by, list) or not superseded_by:
            raise ValueError(f"{label}.superseded_by must be null or a non-empty ID list")
        for index, item in enumerate(superseded_by):
            if not isinstance(item, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item):
                raise ValueError(f"{label}.superseded_by[{index}] must be a stable ID slug")

    reactivated_at = record["reactivated_at"]
    if reactivated_at is not None:
        reactivated_date = parse_date(reactivated_at, f"{label}.reactivated_at")
        if reactivated_date < first_seen or reactivated_date > last_verified:
            raise ValueError(f"{label} reactivated_at must fall between first_seen and last_verified")
        if retired_date is not None and reactivated_date > retired_date:
            raise ValueError(f"{label} reactivated_at must not be after retired_at while retired")


def validate_relevance(record: dict, label: str) -> None:
    relevance = record["relevance"]
    required = {"classification", "public_reason", "signals", "assessed_at", "assessed_by"}
    if set(relevance) != required:
        raise ValueError(f"{label}.relevance has an unexpected shape")
    if relevance["classification"] not in RELEVANCE_CLASSES:
        raise ValueError(f"{label}.relevance.classification is unsupported")
    if not isinstance(relevance["public_reason"], str) or not relevance["public_reason"].strip():
        raise ValueError(f"{label}.relevance.public_reason is required")
    signals = relevance["signals"]
    if not isinstance(signals, list) or not signals or len(signals) != len(set(signals)):
        raise ValueError(f"{label}.relevance.signals must be a unique non-empty list")
    for signal in signals:
        if not isinstance(signal, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", signal):
            raise ValueError(f"{label}.relevance.signals must be public slugs")
    parse_timestamp(relevance["assessed_at"], f"{label}.relevance.assessed_at")
    if relevance["assessed_by"] != "researchassistant":
        raise ValueError(f"{label}.relevance.assessed_by must be researchassistant")


def validate_landscape(record: dict, label: str) -> None:
    landscape = record["landscape"]
    required = {"sponsor_sector", "sponsor_country", "applicant_geographies", "recipient_types", "science_domains"}
    if set(landscape) != required:
        raise ValueError(f"{label}.landscape has an unexpected shape")
    if landscape["sponsor_sector"] not in SPONSOR_SECTORS:
        raise ValueError(f"{label}.landscape.sponsor_sector is unsupported")
    sponsor_country = landscape["sponsor_country"]
    if not re.fullmatch(r"[A-Z]{2}|MULTINATIONAL|UNKNOWN", sponsor_country or ""):
        raise ValueError(f"{label}.landscape.sponsor_country is unsupported")
    for key in ("applicant_geographies", "recipient_types", "science_domains"):
        values = landscape[key]
        if not isinstance(values, list) or not values or len(values) != len(set(values)):
            raise ValueError(f"{label}.landscape.{key} must be a unique non-empty list")
        for value in values:
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label}.landscape.{key} values must be non-empty strings")


def validate_resource_measure(record: dict, measure: dict, label: str) -> None:
    required = {
        "measure_id",
        "resource_kind",
        "accounting_bucket",
        "basis",
        "status_scope",
        "amount",
        "unit",
        "period",
        "source_refs",
        "aggregation",
        "warnings",
    }
    if set(measure) != required:
        raise ValueError(f"{label} has an unexpected shape")
    if measure["resource_kind"] not in RESOURCE_KINDS:
        raise ValueError(f"{label}.resource_kind is unsupported")
    if measure["accounting_bucket"] not in ACCOUNTING_BUCKETS:
        raise ValueError(f"{label}.accounting_bucket is unsupported")
    if measure["status_scope"] not in {"current_only", "archive_only", "current_and_archive"}:
        raise ValueError(f"{label}.status_scope is unsupported")
    amount = measure["amount"]
    if set(amount) != {"minimum", "maximum", "exact", "display"}:
        raise ValueError(f"{label}.amount has an unexpected shape")
    exact = amount["exact"]
    minimum = amount["minimum"]
    maximum = amount["maximum"]
    for value_name, value in (("minimum", minimum), ("maximum", maximum), ("exact", exact)):
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0):
            raise ValueError(f"{label}.amount.{value_name} must be a non-negative number or null")
    if exact is not None and (minimum is not None or maximum is not None):
        raise ValueError(f"{label}.amount.exact is mutually exclusive with min/max")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError(f"{label}.amount minimum exceeds maximum")
    unit = measure["unit"]
    if set(unit) != {"kind", "code", "native_label"}:
        raise ValueError(f"{label}.unit has an unexpected shape")
    if unit["kind"] not in {"currency", "credit_currency", "allocation_credit", "qpu_hour", "node_hour", "service_credit", "count", "unknown"}:
        raise ValueError(f"{label}.unit.kind is unsupported")
    period = measure["period"]
    if set(period) != {"kind", "display"} or period["kind"] not in {"one_time", "annual", "cycle", "project_duration", "unknown"}:
        raise ValueError(f"{label}.period is unsupported")
    source_refs = measure["source_refs"]
    if not isinstance(source_refs, list) or not source_refs:
        raise ValueError(f"{label}.source_refs must be non-empty")
    official_urls = set(record["official_source_urls"])
    for index, source_ref in enumerate(source_refs):
        if set(source_ref) != {"url", "evidence_type", "retrieved_at"}:
            raise ValueError(f"{label}.source_refs[{index}] has an unexpected shape")
        if source_ref["url"] not in official_urls:
            raise ValueError(f"{label}.source_refs[{index}].url must be an official source URL on the record")
        parse_timestamp(source_ref["retrieved_at"], f"{label}.source_refs[{index}].retrieved_at")
    aggregation = measure["aggregation"]
    if set(aggregation) != {"include_in_rollup", "rollup_group_key", "double_count_key", "dedupe_priority"}:
        raise ValueError(f"{label}.aggregation has an unexpected shape")
    if not isinstance(aggregation["include_in_rollup"], bool):
        raise ValueError(f"{label}.aggregation.include_in_rollup must be boolean")
    if not isinstance(aggregation["dedupe_priority"], int):
        raise ValueError(f"{label}.aggregation.dedupe_priority must be an integer")
    if not isinstance(measure["warnings"], list):
        raise ValueError(f"{label}.warnings must be a list")


def validate_resources(record: dict, label: str) -> None:
    resources = record["resources"]
    required = {"quantification_status", "confidence", "reason", "effective_date", "source_refs", "relationship", "measures"}
    if set(resources) != required:
        raise ValueError(f"{label}.resources has an unexpected shape")
    if resources["quantification_status"] not in QUANTIFICATION_STATUSES:
        raise ValueError(f"{label}.resources.quantification_status is unsupported")
    if resources["confidence"] not in {"official_explicit", "official_partial", "unresolved"}:
        raise ValueError(f"{label}.resources.confidence is unsupported")
    parse_date(resources["effective_date"], f"{label}.resources.effective_date")
    if not isinstance(resources["reason"], str) or not resources["reason"].strip():
        raise ValueError(f"{label}.resources.reason is required")
    relationship = resources["relationship"]
    if set(relationship) != {"parent_id", "rollup_policy"} or relationship["rollup_policy"] not in ROLLUP_POLICIES:
        raise ValueError(f"{label}.resources.relationship is unsupported")
    if relationship["parent_id"] is not None and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", relationship["parent_id"]):
        raise ValueError(f"{label}.resources.relationship.parent_id must be a stable ID or null")
    if not isinstance(resources["source_refs"], list) or not resources["source_refs"]:
        raise ValueError(f"{label}.resources.source_refs must be non-empty")
    official_urls = set(record["official_source_urls"])
    for index, source_ref in enumerate(resources["source_refs"]):
        if set(source_ref) != {"url", "evidence_type", "retrieved_at"}:
            raise ValueError(f"{label}.resources.source_refs[{index}] has an unexpected shape")
        if source_ref["url"] not in official_urls:
            raise ValueError(f"{label}.resources.source_refs[{index}].url must be an official source URL")
        parse_timestamp(source_ref["retrieved_at"], f"{label}.resources.source_refs[{index}].retrieved_at")
    measures = resources["measures"]
    if not isinstance(measures, list):
        raise ValueError(f"{label}.resources.measures must be a list")
    measure_ids = [measure.get("measure_id") for measure in measures]
    if len(measure_ids) != len(set(measure_ids)):
        raise ValueError(f"{label}.resources.measures must have unique measure IDs")
    for index, measure in enumerate(measures):
        validate_resource_measure(record, measure, f"{label}.resources.measures[{index}]")
    if not measures and resources["quantification_status"] not in {"unquantified", "individualized"}:
        raise ValueError(f"{label}.resources quantification status requires measures")


def validate_credit_rollup_compatibility(records: list[dict]) -> None:
    platforms_by_key: dict[str, set[str]] = {}
    records_by_key: dict[str, set[str]] = {}
    for record in records:
        for measure in record["resources"].get("measures", []):
            if measure["accounting_bucket"] != "credit" or not measure["aggregation"]["include_in_rollup"]:
                continue
            key = measure["aggregation"]["rollup_group_key"]
            platforms_by_key.setdefault(key, set()).add(measure["unit"]["native_label"])
            records_by_key.setdefault(key, set()).add(record["id"])
    for key, platforms in platforms_by_key.items():
        if len(platforms) > 1:
            records = ", ".join(sorted(records_by_key[key]))
            raise ValueError(
                f"credit rollup key mixes provider/platform-specific credits without compatibility metadata: {key} ({records})"
            )


def validate_public_data(
    data: dict,
    *,
    allow_legacy_lifecycle: bool = False,
    allow_legacy_schema: bool = False,
    expected_record_count: int | None = None,
    expected_status_counts: dict[str, int] | None = None,
) -> None:
    """Apply release-blocking semantic checks before rendering."""
    legacy_allowed = allow_legacy_schema or allow_legacy_lifecycle
    schema_version = data.get("schema_version")
    expected_top_legacy = {
        "title",
        "schema_version",
        "page_date",
        "page_timezone",
        "verified_at",
        "methodology",
        "counts",
        "opportunities",
    }
    expected_top_current = expected_top_legacy | {"view_policy", "view_counts", "relevance_counts"}
    if schema_version == "1.0.0" and legacy_allowed:
        expected_top = expected_top_legacy
    elif schema_version == "1.1.0":
        expected_top = expected_top_current
    else:
        raise ValueError("unsupported dataset identity or schema version")
    if set(data) != expected_top:
        raise ValueError("canonical dataset has an unexpected top-level shape")
    if data["title"] != "Scientific Resource Tracker":
        raise ValueError("unsupported dataset identity or schema version")
    snapshot = parse_date(data["page_date"], "page_date")
    parse_timestamp(data["verified_at"], "verified_at")
    if data["page_timezone"] != "America/Los_Angeles":
        raise ValueError("unexpected page timezone")

    records = data["opportunities"]
    if not isinstance(records, list):
        raise ValueError("opportunities must be a list")
    if expected_record_count is not None and len(records) != expected_record_count:
        raise ValueError("history snapshot record count must match its index summary")
    ids = [record.get("id") for record in records]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item or "") for item in ids):
        raise ValueError("record IDs must be unique stable slugs")

    observed = Counter(record.get("status") for record in records)
    declared_counts = data["counts"]
    observed_counts = {"total": len(records), **status_counts(records)}
    if declared_counts != observed_counts:
        raise ValueError("declared and observed status counts must match")
    if expected_status_counts is not None and status_counts(records) != expected_status_counts:
        raise ValueError("history snapshot status counts must match its index summary")
    if schema_version == "1.1.0":
        expected_policy = {
            "default_view": "curated",
            "curated_classes": ["direct", "adjacent"],
            "all_scope_label": "complete verified database from documented scans",
        }
        if data["view_policy"] != expected_policy:
            raise ValueError("view policy must match the approved public view contract")
        if data["view_counts"] != view_counts(records):
            raise ValueError("view_counts must be derived from records")
        if data["view_counts"]["all"] != observed_counts:
            raise ValueError("all view counts must match canonical counts")
        if data["relevance_counts"] != relevance_counts(records):
            raise ValueError("relevance_counts must be derived from records")

    archive_cutoff = snapshot + timedelta(days=ARCHIVE_LEAD_DAYS)
    soon_cutoff = snapshot + timedelta(days=CLOSING_SOON_DAYS)
    for index, record in enumerate(records):
        label = f"opportunities[{index}]"
        fields = set(record)
        allowed_shapes = {frozenset(RECORD_FIELDS if schema_version == "1.1.0" else RECORD_FIELDS_V1_0)}
        if legacy_allowed:
            allowed_shapes.add(frozenset(LEGACY_RECORD_FIELDS))
        if frozenset(fields) not in allowed_shapes:
            raise ValueError(f"{label} has an unexpected public field shape")
        if record["group"] not in GROUP_LABELS or record["group_label"] != GROUP_LABELS[record["group"]]:
            raise ValueError(f"{label} has inconsistent group metadata")
        if record["status"] not in STATUS_LABELS:
            raise ValueError(f"{label} has an unsupported status")
        if record["deadline_kind"] not in {"rolling", "fixed", "closed", "tbd"}:
            raise ValueError(f"{label} has an unsupported deadline kind")
        if record["verified_date"] != data["page_date"]:
            raise ValueError(f"{label} has a verification-date mismatch")
        parse_timestamp(record["verified_at"], f"{label}.verified_at")
        if fields >= RECORD_FIELDS:
            validate_lifecycle(record, label)

        next_deadline = record["next_deadline"]
        parsed_deadline = parse_date(next_deadline, f"{label}.next_deadline") if next_deadline else None
        if not allow_legacy_lifecycle:
            status_is_archived = record["status"] == "closed"
            kind_is_archived = record["deadline_kind"] == "closed"
            if status_is_archived != kind_is_archived:
                raise ValueError(f"{label} must keep status=closed and deadline_kind=closed in sync")
            if record["deadline_kind"] == "fixed" and parsed_deadline is None:
                raise ValueError(f"{label} fixed deadlines require a machine-readable next_deadline")
            if (
                record["status"] in {"open", "upcoming"}
                and record["deadline_kind"] == "fixed"
                and parsed_deadline is not None
                and parsed_deadline <= archive_cutoff
            ):
                raise ValueError(
                    f"{label} fixed deadline falls inside the inclusive {ARCHIVE_LEAD_DAYS}-day archive fence"
                )
        expected_soon = bool(
            record["status"] == "open" and parsed_deadline and parsed_deadline <= soon_cutoff
        )
        if record["closing_soon"] is not expected_soon:
            raise ValueError(f"{label} has an inconsistent closing-soon flag")

        application_url = record["application_url"]
        apply_label = record["apply_label"]
        if bool(application_url) != bool(apply_label):
            raise ValueError(f"{label} must pair its application URL and label")
        if application_url:
            if record["status"] != "open":
                raise ValueError(f"{label} exposes an application URL while not open")
            require_public_https(application_url, f"{label}.application_url")
        sources = record["official_source_urls"]
        if not isinstance(sources, list) or not sources or len(sources) != len(set(sources)):
            raise ValueError(f"{label} must have unique official-source URLs")
        for source_index, source_url in enumerate(sources):
            require_public_https(source_url, f"{label}.official_source_urls[{source_index}]")
        if schema_version == "1.1.0":
            validate_relevance(record, label)
            validate_landscape(record, label)
            validate_resources(record, label)
    if schema_version == "1.1.0":
        validate_credit_rollup_compatibility(records)


def summarize_snapshot(data: dict, data_sha: str, source: dict, pulse: dict | None = None) -> dict:
    snapshot_id = snapshot_id_for(data_sha, data["page_date"])
    summary = {
        "snapshot_id": snapshot_id,
        "page_date": data["page_date"],
        "page_timezone": data["page_timezone"],
        "verified_at": data["verified_at"],
        "canonical_data_sha256": data_sha,
        "record_count": len(data["opportunities"]),
        "status_counts": status_counts(data["opportunities"]),
        "stable_id_sha256": stable_id_sha256(data["opportunities"]),
        "data_url": f"{HISTORY_ROOT_RELATIVE}/{snapshot_id}/public_opportunities.json",
        "change_manifest_url": f"{HISTORY_ROOT_RELATIVE}/{snapshot_id}/change_manifest.json",
        "source": source,
    }
    if data["schema_version"] == "1.1.0":
        summary["schema_version"] = data["schema_version"]
        summary["view_counts"] = view_counts(data["opportunities"])
        summary["relevance_counts"] = relevance_counts(data["opportunities"])
        summary["funding_pulse_summary"] = funding_pulse_summary(pulse)
        summary["funding_pulse_url"] = f"{HISTORY_ROOT_RELATIVE}/{snapshot_id}/funding_pulse.json"
        if pulse:
            summary["funding_pulse_sha256"] = sha256_bytes(canonical_json_bytes(pulse))
    return summary


def substantive_changed_fields(previous: dict, current: dict) -> list[str]:
    fields = sorted((set(previous) | set(current)) - OPERATIONAL_LIFECYCLE_FIELDS)
    return [field for field in fields if previous.get(field) != current.get(field)]


def validate_lifecycle_transition(previous: dict, current: dict, review_date: str) -> None:
    """Reject fabricated or internally inconsistent retirement/reactivation events."""
    record_id = current["id"]
    previous_retired = previous.get("retired_at")
    current_retired = current.get("retired_at")
    previous_reactivated = previous.get("reactivated_at")
    current_reactivated = current.get("reactivated_at")

    if previous_retired and current_retired and previous_retired != current_retired:
        raise ValueError(f"{record_id} cannot rewrite an existing retirement date")

    if not previous_retired and current_retired:
        if current_reactivated != previous_reactivated:
            raise ValueError(f"{record_id} retirement cannot fabricate or rewrite a reactivation date")
        if current_retired != review_date:
            raise ValueError(f"{record_id} retirement date must match the current review date")
        if current["status"] != "closed" or current["deadline_kind"] != "closed":
            raise ValueError(f"{record_id} retirement requires archived status and deadline kind")
        if not current.get("retirement_reason"):
            raise ValueError(f"{record_id} retirement requires a visible reason")
        if current.get("application_url") or current.get("apply_label") or current.get("closing_soon"):
            raise ValueError(f"{record_id} retirement must remove actionable controls and flags")

    elif previous_retired and not current_retired:
        if current["status"] not in {"open", "upcoming"} or current["deadline_kind"] == "closed":
            raise ValueError(f"{record_id} reactivation requires an actionable status and deadline kind")
        if current.get("retirement_reason") is not None:
            raise ValueError(f"{record_id} reactivation must clear the retirement reason")
        if current_reactivated != review_date or current_reactivated == previous_reactivated:
            raise ValueError(f"{record_id} reactivation date must be newly set to the review date")

    elif current_reactivated != previous_reactivated:
        if current_reactivated is None:
            raise ValueError(f"{record_id} cannot erase a recorded reactivation date")
        if current_retired:
            raise ValueError(f"{record_id} cannot manufacture reactivation while still retired")
        if previous.get("status") != "closed" or current["status"] not in {"open", "upcoming"}:
            raise ValueError(f"{record_id} reactivation requires a real archived-to-actionable transition")
        if current["deadline_kind"] == "closed" or current.get("retirement_reason") is not None:
            raise ValueError(f"{record_id} reactivation must restore actionable lifecycle fields")
        if current_reactivated != review_date:
            raise ValueError(f"{record_id} reactivation date must match the current review date")

    elif previous.get("status") in {"open", "upcoming"} and current["status"] == "closed":
        raise ValueError(f"{record_id} actionable-to-archived transition requires retired_at")
    elif previous.get("status") == "closed" and current["status"] in {"open", "upcoming"}:
        raise ValueError(f"{record_id} archived-to-actionable transition requires a fresh reactivated_at")


def diff_snapshots(previous: dict | None, current: dict, snapshot_id: str) -> dict:
    current_by_id = {record["id"]: record for record in current["opportunities"]}
    previous_by_id = {record["id"]: record for record in previous["opportunities"]} if previous else {}
    missing = sorted(set(previous_by_id) - set(current_by_id))
    if missing:
        raise ValueError(f"snapshot would remove previously published IDs: {', '.join(missing)}")

    changes = []
    counts = {key: 0 for key in ("added", "changed", "retired", "reactivated", "unchanged")}
    relevance_changed = 0
    resources_changed = 0
    for record_id in sorted(current_by_id):
        current_record = current_by_id[record_id]
        previous_record = previous_by_id.get(record_id)
        if previous_record is None:
            change_type = "added"
            changed_fields: list[str] = []
        else:
            validate_lifecycle_transition(previous_record, current_record, current["page_date"])
            changed_fields = substantive_changed_fields(previous_record, current_record)
            was_retired = bool(previous_record.get("retired_at"))
            is_retired = bool(current_record.get("retired_at"))
            if not was_retired and is_retired:
                change_type = "retired"
            elif was_retired and not is_retired:
                change_type = "reactivated"
            elif current_record.get("reactivated_at") != previous_record.get("reactivated_at") and current_record.get("reactivated_at"):
                change_type = "reactivated"
            elif changed_fields:
                change_type = "changed"
            else:
                change_type = "unchanged"
        counts[change_type] += 1
        if "relevance" in changed_fields:
            relevance_changed += 1
        if "resources" in changed_fields:
            resources_changed += 1
        changes.append(
            {
                "id": record_id,
                "change_type": change_type,
                "previous_status": previous_record.get("status") if previous_record else None,
                "current_status": current_record["status"],
                "changed_fields": changed_fields,
                "previous_relevance": previous_record.get("relevance", {}).get("classification") if previous_record else None,
                "current_relevance": current_record.get("relevance", {}).get("classification"),
                "view_membership_changed": (
                    record_is_curated(previous_record) if previous_record else None
                ) != record_is_curated(current_record),
                "funding_quantification_changed": "resources" in changed_fields,
                "previous_record_sha256": record_fingerprint(previous_record) if previous_record else None,
                "current_record_sha256": record_fingerprint(current_record),
            }
        )

    if current["schema_version"] == "1.1.0":
        counts["relevance_changed"] = relevance_changed
        counts["funding_quantification_changed"] = resources_changed
    return {
        "schema_version": CHANGE_MANIFEST_VERSION,
        "snapshot_id": snapshot_id,
        "previous_snapshot_id": snapshot_id_for(sha256_bytes(canonical_json_bytes(previous)), previous["page_date"]) if previous else None,
        "comparison_basis": "stable-id-lifecycle-relevance-funding" if current["schema_version"] == "1.1.0" else "stable-id-lifecycle",
        "current_data_sha256": sha256_bytes(canonical_json_bytes(current)),
        "previous_data_sha256": sha256_bytes(canonical_json_bytes(previous)) if previous else None,
        "counts": counts,
        "funding_pulse_delta": {
            "baseline_available": bool(previous and previous.get("schema_version") == "1.1.0"),
            "baseline_reason": None if previous and previous.get("schema_version") == "1.1.0" else "previous_snapshot_has_no_funding_pulse",
            "program_pool_bucket_changes": [],
            "per_award_bucket_changes": [],
            "in_kind_bucket_changes": [],
            "coverage_delta": {},
        } if current["schema_version"] == "1.1.0" else None,
        "changes": changes,
    }


def load_history(
    history_dir: Path,
    *,
    check_source_custody: bool = True,
    check_funding_pulse: bool = True,
    repo_root: Path | None = None,
) -> tuple[dict, dict[str, tuple[dict, bytes, dict]]]:
    index_path = history_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    validate_history_index(index)
    snapshots: dict[str, tuple[dict, bytes, dict]] = {}
    for item in index["snapshots"]:
        snapshot_id = item["snapshot_id"]
        validate_snapshot_id(snapshot_id)
        snapshot_dir = history_dir / "snapshots" / snapshot_id
        data_bytes = (snapshot_dir / "public_opportunities.json").read_bytes()
        data = json.loads(data_bytes.decode("utf-8"))
        validate_public_data(
            data,
            allow_legacy_lifecycle=True,
            allow_legacy_schema=True,
            expected_record_count=item["record_count"],
            expected_status_counts=item["status_counts"],
        )
        if sha256_bytes(data_bytes) != item["canonical_data_sha256"]:
            raise ValueError(f"history snapshot hash mismatch: {snapshot_id}")
        if stable_id_sha256(data["opportunities"]) != item["stable_id_sha256"]:
            raise ValueError(f"history stable-ID hash mismatch: {snapshot_id}")
        manifest_bytes = (snapshot_dir / "change_manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        validate_change_manifest(manifest, item, data)
        if check_source_custody:
            validate_source_custody(item, repo_root=repo_root)
        if check_funding_pulse:
            validate_funding_pulse_sidecar(
                history_dir=history_dir,
                summary=item,
                data=data,
                manifest=manifest,
                manifest_bytes=manifest_bytes,
                source_repository=index["generated_from"]["source_repository"],
            )
        snapshots[snapshot_id] = (data, data_bytes, manifest)
    return index, snapshots


def validate_history_index(index: dict) -> None:
    if set(index) != {"schema_version", "title", "current_snapshot_id", "generated_from", "snapshots"}:
        raise ValueError("history index has an unexpected shape")
    if index["schema_version"] not in {"1.0.0", SNAPSHOT_INDEX_VERSION}:
        raise ValueError("unsupported history index version")
    validate_snapshot_id(index["current_snapshot_id"], "current_snapshot_id")
    snapshots = index["snapshots"]
    if not snapshots or index["current_snapshot_id"] != snapshots[0]["snapshot_id"]:
        raise ValueError("history index current snapshot must be first")
    seen: set[str] = set()
    previous_key = None
    for item in snapshots:
        required_legacy = {
            "snapshot_id", "page_date", "page_timezone", "verified_at", "canonical_data_sha256",
            "record_count", "status_counts", "stable_id_sha256", "data_url", "change_manifest_url", "source",
        }
        required_current = required_legacy | {
            "schema_version",
            "view_counts",
            "relevance_counts",
            "funding_pulse_summary",
            "funding_pulse_url",
            "funding_pulse_sha256",
        }
        if frozenset(item) not in {frozenset(required_legacy), frozenset(required_current)}:
            raise ValueError("history index snapshot has an unexpected shape")
        if item["snapshot_id"] in seen:
            raise ValueError("duplicate history snapshot ID")
        validate_snapshot_id(item["snapshot_id"])
        seen.add(item["snapshot_id"])
        if item["data_url"] != f"{HISTORY_ROOT_RELATIVE}/{item['snapshot_id']}/public_opportunities.json":
            raise ValueError("history data URL must be root-relative")
        if item["change_manifest_url"] != f"{HISTORY_ROOT_RELATIVE}/{item['snapshot_id']}/change_manifest.json":
            raise ValueError("history manifest URL must be root-relative")
        if "funding_pulse_url" in item and item["funding_pulse_url"] != f"{HISTORY_ROOT_RELATIVE}/{item['snapshot_id']}/funding_pulse.json":
            raise ValueError("history funding pulse URL must be root-relative")
        key = (item["page_date"], item["verified_at"], item["canonical_data_sha256"])
        if previous_key is not None and key > previous_key:
            raise ValueError("history snapshots must be newest first")
        previous_key = key
    if index["current_snapshot_id"] not in seen:
        raise ValueError("history current snapshot ID is missing")


def validate_change_manifest(manifest: dict, summary: dict, data: dict) -> None:
    required_legacy = {
        "schema_version", "snapshot_id", "previous_snapshot_id", "comparison_basis",
        "current_data_sha256", "previous_data_sha256", "counts", "changes",
    }
    required_current = required_legacy | {"funding_pulse_delta"}
    is_current = data.get("schema_version") == "1.1.0"
    if set(manifest) != (required_current if is_current else required_legacy):
        raise ValueError("change manifest has an unexpected shape")
    if manifest["schema_version"] not in {"1.0.0", CHANGE_MANIFEST_VERSION}:
        raise ValueError("change manifest has an unsupported version")
    if manifest["snapshot_id"] != summary["snapshot_id"]:
        raise ValueError("change manifest snapshot mismatch")
    if manifest["current_data_sha256"] != summary["canonical_data_sha256"]:
        raise ValueError("change manifest data SHA mismatch")
    allowed = {"added", "changed", "retired", "reactivated", "unchanged"}
    allowed_counts = allowed | ({"relevance_changed", "funding_quantification_changed"} if is_current else set())
    if set(manifest["counts"]) != allowed:
        if set(manifest["counts"]) != allowed_counts:
            raise ValueError("change manifest counts have an unexpected shape")
    ids = {record["id"] for record in data["opportunities"]}
    observed = Counter()
    for change in manifest["changes"]:
        if change["id"] not in ids:
            raise ValueError("change manifest references an unknown ID")
        if change["change_type"] not in allowed:
            raise ValueError("change manifest has an unsupported change type")
        observed[change["change_type"]] += 1
        if not isinstance(change["changed_fields"], list):
            raise ValueError("change manifest changed_fields must be a list")
    if dict(observed) != {key: manifest["counts"][key] for key in allowed if manifest["counts"][key]}:
        raise ValueError("change manifest counts do not match entries")


def copy_history_artifacts(profile_dir: Path, history_dir: Path, index: dict) -> dict[str, str]:
    output_hashes: dict[str, str] = {}
    snapshots_dir = profile_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    index_bytes = canonical_json_bytes(index)
    (snapshots_dir / "index.json").write_bytes(index_bytes)
    output_hashes["snapshots/index.json"] = sha256_bytes(index_bytes)
    for item in index["snapshots"]:
        snapshot_id = item["snapshot_id"]
        source_dir = history_dir / "snapshots" / snapshot_id
        target_dir = snapshots_dir / snapshot_id
        target_dir.mkdir(parents=True, exist_ok=True)
        names = ["public_opportunities.json", "change_manifest.json"]
        if (source_dir / "funding_pulse.json").exists():
            names.append("funding_pulse.json")
        for name in names:
            payload = (source_dir / name).read_bytes()
            (target_dir / name).write_bytes(payload)
            output_hashes[f"snapshots/{snapshot_id}/{name}"] = sha256_bytes(payload)
    return output_hashes


def validate_profiles(config: dict) -> dict[str, dict[str, str]]:
    if set(config) != {"schema_version", "profiles"} or config["schema_version"] != "1.0.0":
        raise ValueError("unsupported site-profile document")
    profiles = config["profiles"]
    if set(profiles) != set(EXPECTED_PROFILE_URLS):
        raise ValueError("site profiles must contain exactly primary and mirror")
    required = {
        "site_name",
        "site_role",
        "canonical_url",
        "alternate_url",
        "alternate_label",
        "source_repository_url",
    }
    for profile_id, profile in profiles.items():
        if set(profile) != required:
            raise ValueError(f"profile {profile_id} has an unexpected shape")
        if profile["canonical_url"] != EXPECTED_PROFILE_URLS[profile_id]:
            raise ValueError(f"profile {profile_id} has the wrong canonical URL")
        if profile["source_repository_url"] != EXPECTED_SOURCE_REPOSITORY_URL:
            raise ValueError(f"profile {profile_id} has the wrong source repository URL")
        other_id = "mirror" if profile_id == "primary" else "primary"
        if profile["alternate_url"] != EXPECTED_PROFILE_URLS[other_id]:
            raise ValueError(f"profile {profile_id} has the wrong alternate URL")
        require_public_https(profile["canonical_url"], f"profiles.{profile_id}.canonical_url")
        require_public_https(profile["alternate_url"], f"profiles.{profile_id}.alternate_url")
        require_public_https(profile["source_repository_url"], f"profiles.{profile_id}.source_repository_url")
    return profiles


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def source_links(record: dict) -> str:
    links = []
    for index, url in enumerate(record["official_source_urls"], start=1):
        label = "Official information" if index == 1 else f"Official source {index}"
        links.append(
            f'<a class="source-link" href="{esc(url)}" rel="noopener">{label}'
            '<span aria-hidden="true"> ↗</span></a>'
        )
    return "".join(links)


def relevance_badge(record: dict) -> str:
    relevance = record.get("relevance")
    if not relevance:
        return '<span class="badge badge-group">Legacy curated snapshot</span>'
    label = {
        "direct": "Direct fit",
        "adjacent": "Adjacent fit",
        "unrelated": "All-only",
    }[relevance["classification"]]
    return f'<span class="badge badge-relevance">{esc(label)}</span>'


def card_html(record: dict, archived: bool = False) -> str:
    status = record["status"]
    badges = [f'<span class="badge badge-{status}">{STATUS_LABELS[status]}</span>']
    if record["closing_soon"]:
        badges.append('<span class="badge badge-soon">Closing soon</span>')
    badges.append(relevance_badge(record))
    if archived:
        badges.append(f'<span class="badge badge-group">{esc(record["group_label"])}</span>')

    actions = []
    if record["application_url"] and not archived:
        actions.append(
            f'<a class="button apply-link" href="{esc(record["application_url"])}" rel="noopener">'
            f'{esc(record["apply_label"])}<span aria-hidden="true"> ↗</span></a>'
        )
    actions.append(source_links(record))
    search_blob = " ".join(
        str(record.get(key) or "")
        for key in (
            "program", "provider", "group_label", "amount", "deadline", "eligibility",
            "endpoint_note", "retirement_reason",
        )
    ).lower()
    if record.get("relevance"):
        search_blob += " " + " ".join(record["relevance"]["signals"]).lower()
    if record.get("resources"):
        search_blob += " " + record["resources"]["quantification_status"].lower()
        search_blob += " " + " ".join(measure["resource_kind"] for measure in record["resources"].get("measures", []))
    retirement_line = ""
    if archived and record.get("retired_at") and record.get("retirement_reason"):
        retirement_line = (
            f'  <p class="retirement"><strong>Archived {esc(record["retired_at"])}:</strong> '
            f'{esc(record["retirement_reason"])}</p>\n'
        )
    data_view = "curated all" if record_is_curated(record) else "all"
    hidden_class = "" if record_is_curated(record) else " is-hidden"
    return f'''<article class="card card-{status}{hidden_class}" id="card-{esc(record["id"])}" data-status="{status}" data-group="{record["group"]}" data-view="{data_view}" data-search="{esc(search_blob)}">
  <div class="card-top">
    <p class="provider">{esc(record["provider"])}</p>
    <p class="badges">{"".join(badges)}</p>
  </div>
  <h3>{esc(record["program"])}</h3>
  <dl class="facts">
    <div><dt>Resources</dt><dd>{esc(record["amount"])}</dd></div>
    <div><dt>Deadline</dt><dd>{esc(record["deadline"])}</dd></div>
    <div><dt>Fit</dt><dd>{esc(record.get("relevance", {}).get("public_reason", "Legacy curated snapshot predates relevance classification."))}</dd></div>
  </dl>
{retirement_line}  <details>
    <summary>Eligibility and verification notes</summary>
    <p><strong>Eligibility:</strong> {esc(record["eligibility"])}</p>
    <p>{esc(record["endpoint_note"])}</p>
    <p class="all-sources"><strong>Evidence:</strong> {source_links(record)}</p>
  </details>
  <p class="actions">{"".join(actions)}</p>
  <p class="verified">Verified {esc(record["verified_date"])} ({esc(record["page_timezone"] if "page_timezone" in record else "Pacific")})</p>
</article>'''


def active_sections(records: list[dict]) -> str:
    sections = []
    for group, label in GROUP_LABELS.items():
        group_records = [record for record in records if record["group"] == group and record["status"] != "closed"]
        open_count = sum(record["status"] == "open" for record in group_records)
        upcoming_count = sum(record["status"] == "upcoming" for record in group_records)
        count_parts = [f"{open_count} open"]
        if upcoming_count:
            count_parts.append(f"{upcoming_count} upcoming")
        cards = "\n".join(card_html(record) for record in group_records)
        sections.append(f'''<section class="resource-group" data-resource-group="{group}" aria-labelledby="heading-{group}">
  <header class="section-heading">
    <h2 id="heading-{group}">{esc(label)}</h2>
    <p>{" · ".join(count_parts)}</p>
  </header>
  <div class="card-grid">
{cards}
  </div>
</section>''')
    return "\n".join(sections)


WARNING_LABELS = {
    "alternative_award_type_not_summed": "Alternative award type; shown separately and not summed.",
    "compute_access_not_cash": "Compute access, not cash.",
    "contingent_prize_not_program_pool": "Contingent prize; not a program pool.",
    "historical_cumulative_total_excluded": "Historical cumulative totals are excluded from current totals.",
    "in_kind_components_unquantified": "In-kind components are unquantified.",
    "per_award_ceiling_not_program_pool": "Per-award ceiling; not a program pool.",
    "per_award_prize_not_program_pool": "Per-recipient prize; not a program pool.",
    "per_award_range_not_program_pool": "Per-award range; not a program pool.",
    "prize_envelope_not_grant_pool": "Prize envelope; not a grant pool.",
    "provider_specific_credit_not_fungible": "Provider-specific credit; not fungible with other providers.",
    "service_credit_not_cash": "Service credit, not cash.",
}

SCOPE_LABELS = {
    "us_sponsor": "U.S. sponsor current record",
    "all_only": "International or non-U.S.-sponsor record; visible in All Opportunities only",
}

ACCOUNTING_LABELS = {
    "program_pool": "Program pool",
    "per_award": "Per-award amount",
    "credit": "Provider service credit",
    "compute": "Compute access",
    "facility": "Facility access",
    "prize": "Prize",
    "fellowship": "Fellowship",
    "other_in_kind": "Other in-kind support",
}

MEASURE_LABELS = {
    "annual-credit-usd": "Annual credit range",
    "conference-cap-usd": "Conference grant cap",
    "credit-ceiling-usd": "Credit ceiling",
    "grand-prize-eur": "Grand prize",
    "per-project-usd": "Per-project award range",
    "program-pool-usd": "Round budget",
    "typical-qpu-hours": "Typical QPU-hours allocation",
    "workshop-cap-usd": "Workshop grant cap",
}


def fallback_public_label(value: str | None) -> str:
    if not value:
        return "Official measure"
    cleaned = re.sub(r"-(usd|eur|gbp)$", "", value)
    return cleaned.replace("-", " ").replace("_", " ").capitalize()


def pulse_amount_text(measure: dict | None, entry: dict) -> str:
    amount = (measure or {}).get("amount") or {}
    return amount.get("display") or "Official amount not quantified"


def pulse_period_text(measure: dict | None, entry: dict) -> str:
    period = (measure or {}).get("period") or entry.get("period") or {}
    return period.get("display") or "Not stated"


def pulse_record_measure(data: dict, record_id: str, measure_id: str) -> tuple[dict | None, dict | None]:
    record = next((item for item in data["opportunities"] if item["id"] == record_id), None)
    if not record:
        return None, None
    measure = next((item for item in record.get("resources", {}).get("measures", []) if item["measure_id"] == measure_id), None)
    return record, measure


def pulse_measure_label(measure: dict | None) -> str:
    if not measure:
        return "Official measure"
    suffix = measure["measure_id"].split(":")[-1]
    return MEASURE_LABELS.get(suffix, fallback_public_label(suffix))


def pulse_warning_text(measure: dict | None, entry: dict) -> str:
    warnings = (measure or {}).get("warnings") or []
    if warnings:
        return " ".join(WARNING_LABELS.get(item, fallback_public_label(item)) for item in warnings)
    return "Aggregated only with matching resource kind, unit, basis, period, and current status."


def pulse_entry_cards(data: dict, entry: dict) -> str:
    cards = []
    references = entry.get("records") or [{"id": None, "measure_id": None}]
    for reference in references:
        record, measure = pulse_record_measure(data, reference.get("id"), reference.get("measure_id"))
        title = entry["label"]
        if record and measure:
            title = f'{record["provider"]} — {record["program"]}'
        unit = ((measure or {}).get("unit") or entry.get("unit") or {}).get("native_label", "")
        scope = SCOPE_LABELS.get(entry.get("scope"), fallback_public_label(entry.get("scope")))
        accounting = ACCOUNTING_LABELS.get(entry.get("accounting_bucket"), fallback_public_label(entry.get("accounting_bucket")))
        cards.append(f'''<div class="pulse-entry">
  <h4>{esc(title)}</h4>
  <dl>
    <dt>Measure</dt><dd>{esc(pulse_measure_label(measure))}</dd>
    <dt>Amount</dt><dd>{esc(pulse_amount_text(measure, entry))}</dd>
    <dt>Period</dt><dd>{esc(pulse_period_text(measure, entry))}</dd>
    <dt>Unit</dt><dd>{esc(unit)}</dd>
    <dt>Scope</dt><dd>{esc(scope)}</dd>
    <dt>Accounting</dt><dd>{esc(accounting)}</dd>
    <dt>Caveat</dt><dd>{esc(pulse_warning_text(measure, entry))}</dd>
  </dl>
</div>''')
    return "\n".join(cards)


def funding_pulse_html(pulse: dict | None, data: dict | None = None) -> str:
    if not pulse:
        return '''<section class="funding-pulse" id="funding-pulse" aria-labelledby="funding-pulse-heading">
  <h2 id="funding-pulse-heading">Funding Pulse</h2>
  <p>Funding Pulse is unavailable for this legacy snapshot; the snapshot predates structured resources and full-universe relevance classification.</p>
</section>'''
    coverage = pulse["coverage"]
    categories = []
    for label, key in (
        ("Official program pools", "program_pools"),
        ("Per-award ranges", "per_award_ranges"),
        ("Provider-specific credits", "credits"),
        ("Compute and facility", "compute_facility"),
        ("Prizes and fellowships", "prizes_fellowships"),
        ("Other in-kind", "other_in_kind"),
    ):
        entries = pulse.get(key, [])
        if entries:
            details = "\n".join(pulse_entry_cards(data or {"opportunities": []}, entry) for entry in entries)
        else:
            details = '<p class="pulse-empty">No quantified current bucket.</p>'
        categories.append(f'''<section class="pulse-category">
  <h3>{esc(label)}</h3>
  {details}
</section>''')
    baseline_warning = ""
    if not pulse["snapshot_delta"].get("baseline_available"):
        baseline_warning = (
            '<p class="retirement"><strong>No comparable Funding Pulse baseline for this legacy snapshot.</strong> '
            "Previous snapshot has no Funding Pulse sidecar.</p>"
        )
    return f'''<section class="funding-pulse" id="funding-pulse" aria-labelledby="funding-pulse-heading">
  <h2 id="funding-pulse-heading">Funding Pulse</h2>
  <p>Funding Pulse measures officially quantified resources visible in this maintained database snapshot. It is not an estimate of all funding in existence.</p>
  <ul class="pulse-counts">
    <li><strong>{pulse["record_totals"]["total"]}</strong> verified records</li>
    <li><strong>{pulse["record_totals"]["current"]}</strong> current open/upcoming</li>
    <li><strong>{coverage["quantified_current_records"]}</strong> Quantified current records</li>
    <li><strong>{coverage["partially_quantified_current_records"]}</strong> Partially quantified current records</li>
    <li><strong>{coverage["unquantified_current_records"]}</strong> Unquantified current records</li>
  </ul>
  <p class="pulse-note">{esc(coverage["unknown_coverage_note"])}</p>
  {baseline_warning}
  <div class="pulse-categories">
    {"".join(categories)}
  </div>
  <ul class="pulse-warnings">
    {"".join(f"<li>{esc(warning)}</li>" for warning in pulse["warnings"])}
    <li>Provider/platform-specific credits are shown separately and are not fungible across AWS, DigitalOcean, Microsoft Azure, or any other provider.</li>
  </ul>
</section>'''


def closing_soon_html(records: list[dict]) -> str:
    records = sorted((record for record in records if record["closing_soon"]), key=lambda item: item["next_deadline"])
    if not records:
        return "No actionable records were marked as closing soon in this snapshot."
    return " · ".join(
        f'<a href="#card-{esc(record["id"])}">{esc(record["program"])} — '
        f'<time datetime="{esc(record["next_deadline"])}">{esc(record["next_deadline"])}</time></a>'
        for record in records
    )


def render_html(template: str, data: dict, profile_id: str, profile: dict) -> str:
    records = data["opportunities"]
    default_records = view_records(records, data.get("view_policy", {}).get("default_view", "all"))
    counts = {"total": len(default_records), **status_counts(default_records)}
    archive = "\n".join(card_html(record, archived=True) for record in records if record["status"] == "closed")
    pulse = build_funding_pulse(data)
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    replacements = {
        "PROFILE_ID": profile_id,
        "SITE_NAME": esc(profile["site_name"]),
        "SITE_ROLE": esc(profile["site_role"]),
        "CANONICAL_URL": esc(profile["canonical_url"]),
        "ALTERNATE_URL": esc(profile["alternate_url"]),
        "ALTERNATE_LABEL": esc(profile["alternate_label"]),
        "SOURCE_REPOSITORY_URL": esc(profile["source_repository_url"]),
        "PAGE_DATE": esc(data["page_date"]),
        "TIMEZONE": esc(data["page_timezone"]),
        "TOTAL_COUNT": str(counts["total"]),
        "OPEN_COUNT": str(counts["open"]),
        "UPCOMING_COUNT": str(counts["upcoming"]),
        "CLOSED_COUNT": str(counts["closed"]),
        "ACTIVE_COUNT": str(counts["open"] + counts["upcoming"]),
        "CLOSING_SOON": closing_soon_html(default_records),
        "ACTIVE_SECTIONS": active_sections(records),
        "ARCHIVE_CARDS": archive,
        "FUNDING_PULSE": funding_pulse_html(pulse, data),
        "DATASET_JSON": embedded,
        "EVIDENCE_POLICY": esc(data["methodology"]["evidence_policy"]),
        "ENDPOINT_RULE": esc(data["methodology"]["endpoint_rule"]),
    }
    output = template
    for key, value in replacements.items():
        output = output.replace("{{" + key + "}}", value)
    leftovers = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", output)))
    if leftovers:
        raise ValueError(f"unreplaced template placeholders: {leftovers}")
    return output.rstrip() + "\n"


def manifest_for(
    *,
    data: dict,
    profile_id: str,
    profile: dict,
    data_bytes: bytes,
    schema_bytes: bytes,
    template_bytes: bytes,
    generator_bytes: bytes,
    profiles_bytes: bytes,
    html_bytes: bytes,
    history_outputs: dict[str, str],
    funding_pulse_bytes: bytes | None = None,
) -> dict:
    """Create deterministic, repository-relative provenance without machine details."""
    return {
        "manifest_version": BUILD_SPEC_VERSION,
        "snapshot_date": data["page_date"],
        "verified_at": data["verified_at"],
        "profile": {
            "id": profile_id,
            "role": profile["site_role"],
            "canonical_url": profile["canonical_url"],
            "alternate_url": profile["alternate_url"],
        },
        "canonical_data": {
            "logical_name": "data/opportunities.json",
            "sha256": sha256_bytes(data_bytes),
            "record_count": data["counts"]["total"],
            "status_counts": {
                "open": data["counts"]["open"],
                "upcoming": data["counts"]["upcoming"],
                "closed": data["counts"]["closed"],
            },
        },
        "build_inputs": {
            "data/schema.json": sha256_bytes(schema_bytes),
            "src/generate.py": sha256_bytes(generator_bytes),
            "src/site_profiles.json": sha256_bytes(profiles_bytes),
            "src/template.html": sha256_bytes(template_bytes),
        },
        "outputs": {
            "index.html": sha256_bytes(html_bytes),
            "public_opportunities.json": sha256_bytes(data_bytes),
            **({"funding_pulse.json": sha256_bytes(funding_pulse_bytes)} if funding_pulse_bytes else {}),
            **history_outputs,
        },
    }


def build(data_path: Path, schema_path: Path, profiles_path: Path, template_path: Path, output_dir: Path, history_dir: Path) -> None:
    data_bytes_input = data_path.read_bytes()
    data = json.loads(data_bytes_input.decode("utf-8"))
    validate_public_data(data)
    data_bytes = canonical_json_bytes(data)
    if data_bytes != data_bytes_input:
        raise ValueError("canonical data file must already use deterministic UTF-8 formatting")

    schema_bytes = schema_path.read_bytes()
    json.loads(schema_bytes.decode("utf-8"))
    profiles_bytes = profiles_path.read_bytes()
    profile_config = json.loads(profiles_bytes.decode("utf-8"))
    profiles = validate_profiles(profile_config)
    template_bytes = template_path.read_bytes()
    template = template_bytes.decode("utf-8")
    generator_bytes = Path(__file__).read_bytes()
    history_index, _history_snapshots = load_history(history_dir)
    latest = history_index["snapshots"][0]
    if data_bytes != (history_dir / "snapshots" / latest["snapshot_id"] / "public_opportunities.json").read_bytes():
        raise ValueError(
            "production build refused: data/opportunities.json must byte-match "
            "the latest history snapshot; run src/record_snapshot.py after reviewed data edits"
        )

    for profile_id in ("primary", "mirror"):
        profile = profiles[profile_id]
        profile_dir = output_dir / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        html_bytes = render_html(template, data, profile_id, profile).encode("utf-8")
        history_outputs = copy_history_artifacts(profile_dir, history_dir, history_index)
        funding_pulse_bytes = None
        latest_pulse_path = history_dir / "snapshots" / latest["snapshot_id"] / "funding_pulse.json"
        if latest_pulse_path.exists():
            funding_pulse_bytes = latest_pulse_path.read_bytes()
            (profile_dir / "funding_pulse.json").write_bytes(funding_pulse_bytes)
        manifest = manifest_for(
            data=data,
            profile_id=profile_id,
            profile=profile,
            data_bytes=data_bytes,
            schema_bytes=schema_bytes,
            template_bytes=template_bytes,
            generator_bytes=generator_bytes,
            profiles_bytes=profiles_bytes,
            html_bytes=html_bytes,
            history_outputs=history_outputs,
            funding_pulse_bytes=funding_pulse_bytes,
        )
        (profile_dir / "index.html").write_bytes(html_bytes)
        (profile_dir / "public_opportunities.json").write_bytes(data_bytes)
        (profile_dir / "provenance.json").write_bytes(canonical_json_bytes(manifest))

    print(
        "built primary and mirror: "
        f"total={data['counts']['total']} open={data['counts']['open']} "
        f"upcoming={data['counts']['upcoming']} closed={data['counts']['closed']}"
    )


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=project_root / "data" / "opportunities.json")
    parser.add_argument("--schema", type=Path, default=project_root / "data" / "schema.json")
    parser.add_argument("--profiles", type=Path, default=project_root / "src" / "site_profiles.json")
    parser.add_argument("--template", type=Path, default=project_root / "src" / "template.html")
    parser.add_argument("--output", type=Path, default=project_root / "dist")
    parser.add_argument("--history-dir", type=Path, default=project_root / "data" / "history")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build(args.data, args.schema, args.profiles, args.template, args.output, args.history_dir)


if __name__ == "__main__":
    main()
