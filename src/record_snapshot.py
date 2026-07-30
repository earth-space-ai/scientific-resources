#!/usr/bin/env python3
"""Append an immutable reviewed snapshot from data/opportunities.json.

The recorder is deliberately local and standard-library only. It validates the
current canonical dataset, compares it with the newest source snapshot, refuses
ID removal and overwrite, writes one immutable snapshot directory, writes its
change manifest, and updates data/history/index.json deterministically.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import generate


class RecorderError(RuntimeError):
    """Raised when a snapshot cannot be safely appended."""


def git_head(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RecorderError("could not determine local Git HEAD") from exc
    if result.returncode != 0:
        raise RecorderError(result.stderr.strip() or "could not determine local Git HEAD")
    return result.stdout.strip()


def load_current(data_path: Path) -> tuple[dict, bytes, str]:
    data_bytes_input = data_path.read_bytes()
    data = json.loads(data_bytes_input.decode("utf-8"))
    generate.validate_public_data(data)
    data_bytes = generate.canonical_json_bytes(data)
    if data_bytes != data_bytes_input:
        raise RecorderError("canonical data file must already use deterministic UTF-8 formatting")
    return data, data_bytes, generate.sha256_bytes(data_bytes)


def newest_snapshot_data(history_dir: Path, index: dict) -> dict:
    latest = index["snapshots"][0]
    path = history_dir / "snapshots" / latest["snapshot_id"] / "public_opportunities.json"
    return json.loads(path.read_text(encoding="utf-8"))


def sort_snapshots(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (item["page_date"], item["verified_at"], item["canonical_data_sha256"]),
        reverse=True,
    )


def record_snapshot(
    *,
    data_path: Path,
    history_dir: Path,
    source_commit: str,
    expected_page_date: str | None = None,
) -> str:
    data, data_bytes, data_sha = load_current(data_path)
    if expected_page_date and data["page_date"] != expected_page_date:
        raise RecorderError(f"expected page_date {expected_page_date}, found {data['page_date']}")

    index, _snapshots = generate.load_history(history_dir)
    previous = newest_snapshot_data(history_dir, index)
    snapshot_id = generate.snapshot_id_for(data_sha, data["page_date"])
    generate.validate_snapshot_id(snapshot_id)
    snapshot_dir = history_dir / "snapshots" / snapshot_id
    if snapshot_dir.exists():
        raise RecorderError(f"snapshot already exists and will not be overwritten: {snapshot_id}")

    source = {
        "kind": "source-git",
        "commit": source_commit,
        "path": "data/opportunities.json",
    }
    manifest = generate.diff_snapshots(previous, data, snapshot_id)
    manifest["previous_snapshot_id"] = index["snapshots"][0]["snapshot_id"]
    manifest["previous_data_sha256"] = index["snapshots"][0]["canonical_data_sha256"]
    previous_pulse_path = history_dir / "snapshots" / index["snapshots"][0]["snapshot_id"] / "funding_pulse.json"
    pulse = generate.build_funding_pulse(
        data,
        manifest,
        snapshot_id=snapshot_id,
        baseline_available=previous_pulse_path.exists(),
        baseline_reason=None if previous_pulse_path.exists() else "previous_snapshot_has_no_funding_pulse",
    )
    summary = generate.summarize_snapshot(data, data_sha, source, pulse)

    snapshot_dir.mkdir(parents=False, exist_ok=False)
    (snapshot_dir / "public_opportunities.json").write_bytes(data_bytes)
    (snapshot_dir / "change_manifest.json").write_bytes(generate.canonical_json_bytes(manifest))
    (snapshot_dir / "funding_pulse.json").write_bytes(generate.canonical_json_bytes(pulse))

    snapshots = [summary, *[item for item in index["snapshots"] if item["snapshot_id"] != snapshot_id]]
    snapshots = sort_snapshots(snapshots)
    index["snapshots"] = snapshots
    index["current_snapshot_id"] = snapshots[0]["snapshot_id"]
    index["generated_from"]["source_commit"] = source_commit
    index["generated_from"]["build_spec_version"] = generate.BUILD_SPEC_VERSION
    index["schema_version"] = generate.SNAPSHOT_INDEX_VERSION
    (history_dir / "index.json").write_bytes(generate.canonical_json_bytes(index))
    return snapshot_id


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=project_root / "data" / "opportunities.json")
    parser.add_argument("--history-dir", type=Path, default=project_root / "data" / "history")
    parser.add_argument(
        "--source-commit",
        default=None,
        help="reviewed source Git commit to record; defaults to local HEAD",
    )
    parser.add_argument(
        "--expected-page-date",
        default=None,
        help="optional safety check, for example 2026-07-29 for the next reviewed snapshot",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    project_root = Path(__file__).resolve().parents[1]
    try:
        source_commit = args.source_commit or git_head(project_root)
        snapshot_id = record_snapshot(
            data_path=args.data,
            history_dir=args.history_dir,
            source_commit=source_commit,
            expected_page_date=args.expected_page_date,
        )
    except (RecorderError, ValueError, json.JSONDecodeError) as exc:
        print(f"snapshot rejected: {exc}", file=sys.stderr)
        return 2
    print(f"recorded snapshot {snapshot_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
