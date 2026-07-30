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


def atomic_write(path: Path, payload: bytes) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(payload)
    tmp_path.replace(path)


def pulse_provenance(
    *,
    data_sha: str,
    manifest_sha: str,
    snapshot_id: str,
    source: dict,
    source_repository_url: str,
) -> dict:
    return {
        "canonical_data_sha256": data_sha,
        "change_manifest_sha256": manifest_sha,
        "change_manifest_path": f"data/history/snapshots/{snapshot_id}/change_manifest.json",
        "change_manifest_url": f"{generate.HISTORY_ROOT_RELATIVE}/{snapshot_id}/change_manifest.json",
        "source_git": {
            "source_repository": source_repository_url,
            "commit": source["commit"],
            "path": source["path"],
        },
    }


def build_snapshot_pulse(
    *,
    history_dir: Path,
    index: dict,
    data: dict,
    data_sha: str,
    manifest: dict,
    snapshot_id: str,
    source: dict,
) -> dict:
    manifest_bytes = generate.canonical_json_bytes(manifest)
    previous_snapshot_id = manifest["previous_snapshot_id"]
    previous_pulse_path = history_dir / "snapshots" / previous_snapshot_id / "funding_pulse.json" if previous_snapshot_id else None
    baseline_available = bool(previous_pulse_path and previous_pulse_path.exists())
    provenance = pulse_provenance(
        data_sha=data_sha,
        manifest_sha=generate.sha256_bytes(manifest_bytes),
        snapshot_id=snapshot_id,
        source=source,
        source_repository_url=index["generated_from"]["source_repository"],
    )
    return generate.build_funding_pulse(
        data,
        manifest,
        snapshot_id=snapshot_id,
        provenance=provenance,
        baseline_available=baseline_available,
        baseline_reason=None if baseline_available else "previous_snapshot_has_no_funding_pulse",
    )


def record_snapshot(
    *,
    data_path: Path,
    history_dir: Path,
    source_commit: str,
    source_repo_root: Path | None = None,
    expected_page_date: str | None = None,
) -> str:
    data, data_bytes, data_sha = load_current(data_path)
    if expected_page_date and data["page_date"] != expected_page_date:
        raise RecorderError(f"expected page_date {expected_page_date}, found {data['page_date']}")

    index, _snapshots = generate.load_history(history_dir, repo_root=source_repo_root)
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
    source_bytes = generate.git_show_file_bytes(source_commit, source["path"], repo_root=source_repo_root)
    if generate.sha256_bytes(source_bytes) != data_sha:
        raise RecorderError("source-git commit does not reproduce canonical data bytes")
    manifest["previous_snapshot_id"] = index["snapshots"][0]["snapshot_id"]
    manifest["previous_data_sha256"] = index["snapshots"][0]["canonical_data_sha256"]
    pulse = build_snapshot_pulse(
        history_dir=history_dir,
        index=index,
        data=data,
        data_sha=data_sha,
        manifest=manifest,
        snapshot_id=snapshot_id,
        source=source,
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
    generate.load_history(history_dir, repo_root=source_repo_root)
    return snapshot_id


def finalize_snapshot_provenance(
    *,
    history_dir: Path,
    snapshot_id: str,
    source_commit: str,
    source_repo_root: Path | None = None,
) -> str:
    index, snapshots = generate.load_history(
        history_dir,
        check_source_custody=False,
        check_funding_pulse=False,
        repo_root=source_repo_root,
    )
    summary = next((item for item in index["snapshots"] if item["snapshot_id"] == snapshot_id), None)
    if summary is None:
        raise RecorderError(f"snapshot not found: {snapshot_id}")
    source = {"kind": "source-git", "commit": source_commit, "path": "data/opportunities.json"}
    source_bytes = generate.git_show_file_bytes(source_commit, source["path"], repo_root=source_repo_root)
    if generate.sha256_bytes(source_bytes) != summary["canonical_data_sha256"]:
        raise RecorderError("source-git commit does not reproduce the existing snapshot bytes")

    snapshot_dir = history_dir / "snapshots" / snapshot_id
    data_path = snapshot_dir / "public_opportunities.json"
    manifest_path = snapshot_dir / "change_manifest.json"
    before_data = data_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    data, _data_bytes, manifest = snapshots[snapshot_id]
    if generate.sha256_bytes(before_data) != summary["canonical_data_sha256"]:
        raise RecorderError("existing snapshot data hash does not match index summary")

    index_path = history_dir / "index.json"
    before_index = index_path.read_bytes()
    pulse_path = snapshot_dir / "funding_pulse.json"
    before_pulse = pulse_path.read_bytes() if pulse_path.exists() else None
    summary["source"] = source
    pulse_bytes = None
    if snapshot_id == index["current_snapshot_id"]:
        pulse = build_snapshot_pulse(
            history_dir=history_dir,
            index=index,
            data=data,
            data_sha=summary["canonical_data_sha256"],
            manifest=manifest,
            snapshot_id=snapshot_id,
            source=source,
        )
        pulse_bytes = generate.canonical_json_bytes(pulse)
        summary["funding_pulse_summary"] = generate.funding_pulse_summary(pulse)
        summary["funding_pulse_sha256"] = generate.sha256_bytes(pulse_bytes)
        index["generated_from"]["source_commit"] = source_commit
        index["generated_from"]["build_spec_version"] = generate.BUILD_SPEC_VERSION

    if data_path.read_bytes() != before_data:
        raise RecorderError("provenance finalization attempted to alter immutable snapshot data")
    if manifest_path.read_bytes() != before_manifest:
        raise RecorderError("provenance finalization attempted to alter immutable change manifest")
    index_bytes = generate.canonical_json_bytes(index)
    try:
        if pulse_bytes is not None:
            atomic_write(pulse_path, pulse_bytes)
        atomic_write(index_path, index_bytes)
        generate.validate_source_custody(summary, repo_root=source_repo_root)
        generate.load_history(history_dir, repo_root=source_repo_root)
    except Exception:
        if before_pulse is not None:
            atomic_write(pulse_path, before_pulse)
        elif pulse_path.exists():
            pulse_path.unlink()
        atomic_write(index_path, before_index)
        raise
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
    parser.add_argument(
        "--finalize-provenance-only",
        action="store_true",
        help="repair source-git provenance for an existing immutable snapshot after proving custody",
    )
    parser.add_argument("--snapshot-id", default=None, help="existing snapshot ID for --finalize-provenance-only")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    project_root = Path(__file__).resolve().parents[1]
    try:
        source_commit = args.source_commit or git_head(project_root)
        if args.finalize_provenance_only:
            if not args.snapshot_id:
                raise RecorderError("--snapshot-id is required with --finalize-provenance-only")
            snapshot_id = finalize_snapshot_provenance(
                history_dir=args.history_dir,
                snapshot_id=args.snapshot_id,
                source_commit=source_commit,
            )
        else:
            snapshot_id = record_snapshot(
                data_path=args.data,
                history_dir=args.history_dir,
                source_commit=source_commit,
                expected_page_date=args.expected_page_date,
            )
    except (RecorderError, ValueError, json.JSONDecodeError) as exc:
        print(f"snapshot rejected: {exc}", file=sys.stderr)
        return 2
    verb = "finalized provenance for" if args.finalize_provenance_only else "recorded snapshot"
    print(f"{verb} {snapshot_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
