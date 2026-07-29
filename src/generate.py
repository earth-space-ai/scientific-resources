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
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

BUILD_SPEC_VERSION = "1.0.0"
SNAPSHOT_INDEX_VERSION = "1.0.0"
CHANGE_MANIFEST_VERSION = "1.0.0"
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
STATUS_LABELS = {"open": "Open", "upcoming": "Upcoming", "closed": "Closed"}
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
}
LEGACY_RECORD_FIELDS = RECORD_FIELDS - {
    "first_seen",
    "last_verified",
    "retired_at",
    "retirement_reason",
    "superseded_by",
    "reactivated_at",
}
OPERATIONAL_LIFECYCLE_FIELDS = {"first_seen", "last_verified", "verified_at", "verified_date"}
LIFECYCLE_TRANSITION_FIELDS = {"retired_at", "retirement_reason", "superseded_by", "reactivated_at"}
HISTORY_ROOT_RELATIVE = "/scientific-resources/snapshots"


def canonical_json_bytes(value: object) -> bytes:
    """Return the repository's stable UTF-8 JSON representation."""
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def validate_lifecycle(record: dict, label: str) -> None:
    first_seen = parse_date(record["first_seen"], f"{label}.first_seen")
    last_verified = parse_date(record["last_verified"], f"{label}.last_verified")
    if first_seen > last_verified:
        raise ValueError(f"{label} first_seen must not be after last_verified")
    if record["last_verified"] != record["verified_date"]:
        raise ValueError(f"{label} last_verified must match verified_date")

    retired_at = record["retired_at"]
    retirement_reason = record["retirement_reason"]
    if retired_at is None:
        if retirement_reason is not None:
            raise ValueError(f"{label} retirement_reason requires retired_at")
    else:
        retired_date = parse_date(retired_at, f"{label}.retired_at")
        if retired_date < first_seen:
            raise ValueError(f"{label} retired_at must not be before first_seen")
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
        if reactivated_date < first_seen:
            raise ValueError(f"{label} reactivated_at must not be before first_seen")
        if retired_at is not None and reactivated_date < parse_date(retired_at, f"{label}.retired_at"):
            raise ValueError(f"{label} reactivated_at must not be before retired_at")


def validate_public_data(
    data: dict,
    *,
    allow_legacy_lifecycle: bool = False,
    expected_record_count: int | None = None,
    expected_status_counts: dict[str, int] | None = None,
) -> None:
    """Apply release-blocking semantic checks before rendering."""
    expected_top = {
        "title",
        "schema_version",
        "page_date",
        "page_timezone",
        "verified_at",
        "methodology",
        "counts",
        "opportunities",
    }
    if set(data) != expected_top:
        raise ValueError("canonical dataset has an unexpected top-level shape")
    if data["title"] != "Scientific Resource Tracker" or data["schema_version"] != "1.0.0":
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

    soon_cutoff = snapshot + timedelta(days=10)
    for index, record in enumerate(records):
        label = f"opportunities[{index}]"
        fields = set(record)
        allowed_shapes = {frozenset(RECORD_FIELDS)}
        if allow_legacy_lifecycle:
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


def summarize_snapshot(data: dict, data_sha: str, source: dict) -> dict:
    snapshot_id = snapshot_id_for(data_sha, data["page_date"])
    return {
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


def substantive_changed_fields(previous: dict, current: dict) -> list[str]:
    fields = sorted((set(previous) | set(current)) - OPERATIONAL_LIFECYCLE_FIELDS)
    return [field for field in fields if previous.get(field) != current.get(field)]


def diff_snapshots(previous: dict | None, current: dict, snapshot_id: str) -> dict:
    current_by_id = {record["id"]: record for record in current["opportunities"]}
    previous_by_id = {record["id"]: record for record in previous["opportunities"]} if previous else {}
    missing = sorted(set(previous_by_id) - set(current_by_id))
    if missing:
        raise ValueError(f"snapshot would remove previously published IDs: {', '.join(missing)}")

    changes = []
    counts = {key: 0 for key in ("added", "changed", "retired", "reactivated", "unchanged")}
    for record_id in sorted(current_by_id):
        current_record = current_by_id[record_id]
        previous_record = previous_by_id.get(record_id)
        if previous_record is None:
            change_type = "added"
            changed_fields: list[str] = []
        else:
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
        changes.append(
            {
                "id": record_id,
                "change_type": change_type,
                "previous_status": previous_record.get("status") if previous_record else None,
                "current_status": current_record["status"],
                "changed_fields": changed_fields,
                "previous_record_sha256": record_fingerprint(previous_record) if previous_record else None,
                "current_record_sha256": record_fingerprint(current_record),
            }
        )

    return {
        "schema_version": CHANGE_MANIFEST_VERSION,
        "snapshot_id": snapshot_id,
        "previous_snapshot_id": snapshot_id_for(sha256_bytes(canonical_json_bytes(previous)), previous["page_date"]) if previous else None,
        "comparison_basis": "stable-id-lifecycle",
        "current_data_sha256": sha256_bytes(canonical_json_bytes(current)),
        "previous_data_sha256": sha256_bytes(canonical_json_bytes(previous)) if previous else None,
        "counts": counts,
        "changes": changes,
    }


def load_history(history_dir: Path) -> tuple[dict, dict[str, tuple[dict, bytes, dict]]]:
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
            expected_record_count=item["record_count"],
            expected_status_counts=item["status_counts"],
        )
        if sha256_bytes(data_bytes) != item["canonical_data_sha256"]:
            raise ValueError(f"history snapshot hash mismatch: {snapshot_id}")
        if stable_id_sha256(data["opportunities"]) != item["stable_id_sha256"]:
            raise ValueError(f"history stable-ID hash mismatch: {snapshot_id}")
        manifest = json.loads((snapshot_dir / "change_manifest.json").read_text(encoding="utf-8"))
        validate_change_manifest(manifest, item, data)
        snapshots[snapshot_id] = (data, data_bytes, manifest)
    return index, snapshots


def validate_history_index(index: dict) -> None:
    if set(index) != {"schema_version", "title", "current_snapshot_id", "generated_from", "snapshots"}:
        raise ValueError("history index has an unexpected shape")
    if index["schema_version"] != SNAPSHOT_INDEX_VERSION:
        raise ValueError("unsupported history index version")
    validate_snapshot_id(index["current_snapshot_id"], "current_snapshot_id")
    snapshots = index["snapshots"]
    if not snapshots or index["current_snapshot_id"] != snapshots[0]["snapshot_id"]:
        raise ValueError("history index current snapshot must be first")
    seen: set[str] = set()
    previous_key = None
    for item in snapshots:
        required = {
            "snapshot_id", "page_date", "page_timezone", "verified_at", "canonical_data_sha256",
            "record_count", "status_counts", "stable_id_sha256", "data_url", "change_manifest_url", "source",
        }
        if set(item) != required:
            raise ValueError("history index snapshot has an unexpected shape")
        if item["snapshot_id"] in seen:
            raise ValueError("duplicate history snapshot ID")
        validate_snapshot_id(item["snapshot_id"])
        seen.add(item["snapshot_id"])
        if item["data_url"] != f"{HISTORY_ROOT_RELATIVE}/{item['snapshot_id']}/public_opportunities.json":
            raise ValueError("history data URL must be root-relative")
        if item["change_manifest_url"] != f"{HISTORY_ROOT_RELATIVE}/{item['snapshot_id']}/change_manifest.json":
            raise ValueError("history manifest URL must be root-relative")
        key = (item["page_date"], item["verified_at"], item["canonical_data_sha256"])
        if previous_key is not None and key > previous_key:
            raise ValueError("history snapshots must be newest first")
        previous_key = key
    if index["current_snapshot_id"] not in seen:
        raise ValueError("history current snapshot ID is missing")


def validate_change_manifest(manifest: dict, summary: dict, data: dict) -> None:
    required = {
        "schema_version", "snapshot_id", "previous_snapshot_id", "comparison_basis",
        "current_data_sha256", "previous_data_sha256", "counts", "changes",
    }
    if set(manifest) != required or manifest["schema_version"] != CHANGE_MANIFEST_VERSION:
        raise ValueError("change manifest has an unexpected shape")
    if manifest["snapshot_id"] != summary["snapshot_id"]:
        raise ValueError("change manifest snapshot mismatch")
    if manifest["current_data_sha256"] != summary["canonical_data_sha256"]:
        raise ValueError("change manifest data SHA mismatch")
    allowed = {"added", "changed", "retired", "reactivated", "unchanged"}
    if set(manifest["counts"]) != allowed:
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
        for name in ("public_opportunities.json", "change_manifest.json"):
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


def card_html(record: dict, archived: bool = False) -> str:
    status = record["status"]
    badges = [f'<span class="badge badge-{status}">{STATUS_LABELS[status]}</span>']
    if record["closing_soon"]:
        badges.append('<span class="badge badge-soon">Closing soon</span>')
    if archived:
        badges.append(f'<span class="badge badge-group">{esc(record["group_label"])}</span>')

    actions = []
    if record["application_url"]:
        actions.append(
            f'<a class="button apply-link" href="{esc(record["application_url"])}" rel="noopener">'
            f'{esc(record["apply_label"])}<span aria-hidden="true"> ↗</span></a>'
        )
    actions.append(source_links(record))
    search_blob = " ".join(
        str(record[key])
        for key in ("program", "provider", "group_label", "amount", "deadline", "eligibility", "endpoint_note")
    ).lower()
    return f'''<article class="card card-{status}" id="card-{esc(record["id"])}" data-status="{status}" data-group="{record["group"]}" data-search="{esc(search_blob)}">
  <div class="card-top">
    <p class="provider">{esc(record["provider"])}</p>
    <p class="badges">{"".join(badges)}</p>
  </div>
  <h3>{esc(record["program"])}</h3>
  <dl class="facts">
    <div><dt>Resources</dt><dd>{esc(record["amount"])}</dd></div>
    <div><dt>Deadline</dt><dd>{esc(record["deadline"])}</dd></div>
  </dl>
  <details>
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
        cards = "\n".join(card_html(record) for record in group_records)
        sections.append(f'''<section class="resource-group" data-resource-group="{group}" aria-labelledby="heading-{group}">
  <header class="section-heading">
    <h2 id="heading-{group}">{esc(label)}</h2>
    <p>{open_count} open</p>
  </header>
  <div class="card-grid">
{cards}
  </div>
</section>''')
    return "\n".join(sections)


def closing_soon_html(records: list[dict]) -> str:
    records = sorted((record for record in records if record["closing_soon"]), key=lambda item: item["next_deadline"])
    return " · ".join(
        f'<a href="#card-{esc(record["id"])}">{esc(record["program"])} — '
        f'<time datetime="{esc(record["next_deadline"])}">{esc(record["next_deadline"])}</time></a>'
        for record in records
    )


def render_html(template: str, data: dict, profile_id: str, profile: dict) -> str:
    records = data["opportunities"]
    counts = data["counts"]
    archive = "\n".join(card_html(record, archived=True) for record in records if record["status"] == "closed")
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
        "CLOSING_SOON": closing_soon_html(records),
        "ACTIVE_SECTIONS": active_sections(records),
        "ARCHIVE_CARDS": archive,
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
