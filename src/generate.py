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
EXPECTED_PROFILE_URLS = {
    "primary": "https://earth-space-ai.org/scientific-resources",
    "mirror": "https://huangzesen.github.io/scientific-resources/",
}
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
}


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


def validate_public_data(data: dict) -> None:
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
    if not isinstance(records, list) or len(records) != 32:
        raise ValueError("the reviewed snapshot must contain exactly 32 records")
    ids = [record.get("id") for record in records]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item or "") for item in ids):
        raise ValueError("record IDs must be unique stable slugs")

    observed = Counter(record.get("status") for record in records)
    expected_counts = {"total": len(records), "open": 22, "upcoming": 2, "closed": 8}
    if data["counts"] != expected_counts or observed != Counter({"open": 22, "closed": 8, "upcoming": 2}):
        raise ValueError("declared and observed status counts must match the reviewed snapshot")

    soon_cutoff = snapshot + timedelta(days=10)
    for index, record in enumerate(records):
        label = f"opportunities[{index}]"
        if set(record) != RECORD_FIELDS:
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


def validate_profiles(config: dict) -> dict[str, dict[str, str]]:
    if set(config) != {"schema_version", "profiles"} or config["schema_version"] != "1.0.0":
        raise ValueError("unsupported site-profile document")
    profiles = config["profiles"]
    if set(profiles) != set(EXPECTED_PROFILE_URLS):
        raise ValueError("site profiles must contain exactly primary and mirror")
    required = {"site_name", "site_role", "canonical_url", "alternate_url", "alternate_label"}
    for profile_id, profile in profiles.items():
        if set(profile) != required:
            raise ValueError(f"profile {profile_id} has an unexpected shape")
        if profile["canonical_url"] != EXPECTED_PROFILE_URLS[profile_id]:
            raise ValueError(f"profile {profile_id} has the wrong canonical URL")
        other_id = "mirror" if profile_id == "primary" else "primary"
        if profile["alternate_url"] != EXPECTED_PROFILE_URLS[other_id]:
            raise ValueError(f"profile {profile_id} has the wrong alternate URL")
        require_public_https(profile["canonical_url"], f"profiles.{profile_id}.canonical_url")
        require_public_https(profile["alternate_url"], f"profiles.{profile_id}.alternate_url")
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
        },
    }


def build(data_path: Path, schema_path: Path, profiles_path: Path, template_path: Path, output_dir: Path) -> None:
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

    for profile_id in ("primary", "mirror"):
        profile = profiles[profile_id]
        profile_dir = output_dir / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        html_bytes = render_html(template, data, profile_id, profile).encode("utf-8")
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build(args.data, args.schema, args.profiles, args.template, args.output)


if __name__ == "__main__":
    main()
