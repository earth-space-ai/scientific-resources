#!/usr/bin/env python3
"""Plan or apply an exact local sync into clean primary and mirror checkouts.

This helper performs no network access and uses no credentials. By default it
only validates and prints a plan. Pass --apply to copy the exact reviewed
generated file tree for each host profile into its owner-controlled checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = PROJECT_ROOT / "dist"
ROOT_FILE_NAMES = ("index.html", "public_opportunities.json", "funding_pulse.json", "provenance.json")
DESTINATION_DIRS = {
    "primary": Path("public/scientific-resources"),
    "mirror": Path("scientific-resources"),
}


class SyncError(RuntimeError):
    """Raised when a checkout cannot be proven safe for exact synchronization."""


@dataclass(frozen=True)
class CheckoutPlan:
    profile: str
    checkout: Path
    destination_dir: Path
    file_names: tuple[str, ...]

    @property
    def allowed_paths(self) -> frozenset[str]:
        return frozenset((DESTINATION_DIRS[self.profile] / name).as_posix() for name in self.file_names)

    def source(self, name: str) -> Path:
        return DIST_ROOT / self.profile / name

    def destination(self, name: str) -> Path:
        return self.destination_dir / name


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(checkout: Path, *arguments: str) -> bytes:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", str(checkout), *arguments],
            check=False,
            capture_output=True,
            env=environment,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise SyncError("git is required to verify checkout state") from exc
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"git inspection timed out for {checkout}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SyncError(f"git inspection failed for {checkout}: {detail or 'unknown error'}")
    return result.stdout


def _checkout_root(requested: Path) -> Path:
    try:
        checkout = requested.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise SyncError(f"checkout does not exist: {requested}") from exc
    if not checkout.is_dir():
        raise SyncError(f"checkout is not a directory: {requested}")
    root_text = _git(checkout, "rev-parse", "--show-toplevel").decode("utf-8", errors="strict").strip()
    root = Path(root_text).resolve(strict=True)
    if checkout != root:
        raise SyncError(f"checkout path must be the Git worktree root: {requested}")
    return checkout


def git_status_paths(checkout: Path) -> frozenset[str]:
    """Return all changed/untracked paths from porcelain v1 without quoting."""
    output = _git(checkout, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    fields = output.split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            raise SyncError(f"could not parse git status for {checkout}")
        status = entry[:2].decode("ascii", errors="strict")
        paths.add(os.fsdecode(entry[3:]))
        if "R" in status or "C" in status:
            if index >= len(fields) or not fields[index]:
                raise SyncError(f"could not parse renamed path in git status for {checkout}")
            paths.add(os.fsdecode(fields[index]))
            index += 1
    return frozenset(paths)


def _assert_clean_pre_state(checkout: Path) -> None:
    dirty = git_status_paths(checkout)
    if dirty:
        rendered = ", ".join(sorted(dirty))
        raise SyncError(f"dirty pre-state rejected for {checkout}: {rendered}")


def assert_status_confined(checkout: Path, allowed_paths: frozenset[str]) -> frozenset[str]:
    """Prove every post-apply worktree change is one of the exact output paths."""
    observed = git_status_paths(checkout)
    collateral = observed - allowed_paths
    if collateral:
        rendered = ", ".join(sorted(collateral))
        raise SyncError(f"post-apply git status contains paths outside allowed file set: {rendered}")
    return observed


def _assert_safe_path(checkout: Path, destination: Path) -> None:
    relative = destination.relative_to(checkout)
    cursor = checkout
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SyncError(f"destination path traverses a symlink: {cursor}")
        if cursor.exists() and not cursor.is_dir():
            raise SyncError(f"destination ancestor is not a directory: {cursor}")
    if destination.is_symlink():
        raise SyncError(f"destination file is a symlink: {destination}")
    if destination.exists() and not destination.is_file():
        raise SyncError(f"destination is not a regular file: {destination}")
    if destination.exists() and destination.stat(follow_symlinks=False).st_nlink != 1:
        raise SyncError(f"destination file has hard-link aliases: {destination}")


def _assert_safe_destination(checkout: Path, destination_dir: Path, file_names: tuple[str, ...]) -> None:
    """Reject symlink traversal and non-directory ancestors before any write."""
    relative = destination_dir.relative_to(checkout)
    cursor = checkout
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SyncError(f"destination path traverses a symlink: {cursor}")
        if cursor.exists() and not cursor.is_dir():
            raise SyncError(f"destination ancestor is not a directory: {cursor}")
    for name in file_names:
        _assert_safe_path(checkout, destination_dir / name)


def generated_file_names(profile: str) -> tuple[str, ...]:
    source_dir = DIST_ROOT / profile
    if not source_dir.is_dir():
        raise SyncError(f"missing build profile directory: dist/{profile}")
    names: list[str] = []
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise SyncError(f"generated output is a symlink: {path}")
        if path.is_file():
            relative = path.relative_to(source_dir).as_posix()
            if relative.startswith("../") or relative == "":
                raise SyncError(f"unsafe generated output path: {path}")
            names.append(relative)
    required = set(ROOT_FILE_NAMES) | {"snapshots/index.json"}
    missing = required - set(names)
    if missing:
        raise SyncError(f"required generated outputs are missing for {profile}: {', '.join(sorted(missing))}")
    for name in names:
        if not (name in ROOT_FILE_NAMES or name.startswith("snapshots/")):
            raise SyncError(f"unexpected generated output for exact sync: dist/{profile}/{name}")
    return tuple(names)


def _prepare_plan(profile: str, requested_checkout: Path) -> CheckoutPlan:
    checkout = _checkout_root(requested_checkout)
    _assert_clean_pre_state(checkout)
    source_dir = DIST_ROOT / profile
    file_names = generated_file_names(profile)
    for name in file_names:
        source = source_dir / name
        if source.is_symlink() or not source.is_file():
            raise SyncError(f"required {profile} build file is missing or unsafe: dist/{profile}/{name}")
    destination_dir = checkout / DESTINATION_DIRS[profile]
    _assert_safe_destination(checkout, destination_dir, file_names)
    return CheckoutPlan(profile=profile, checkout=checkout, destination_dir=destination_dir, file_names=file_names)


def _copy_exact(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)


def _assert_byte_and_hash_parity(plan: CheckoutPlan) -> None:
    for name in plan.file_names:
        source = plan.source(name)
        destination = plan.destination(name)
        if not destination.is_file() or destination.is_symlink():
            raise SyncError(f"copied destination is missing or unsafe: {destination}")
        if source.read_bytes() != destination.read_bytes():
            raise SyncError(f"byte parity failed for {plan.profile}/{name}")
        if sha256_path(source) != sha256_path(destination):
            raise SyncError(f"SHA-256 parity failed for {plan.profile}/{name}")


def synchronize(primary_checkout: Path, mirror_checkout: Path, *, apply: bool = False) -> tuple[CheckoutPlan, CheckoutPlan]:
    """Validate both checkouts, then plan or apply the exact generated-file sync."""
    primary = _prepare_plan("primary", primary_checkout)
    mirror = _prepare_plan("mirror", mirror_checkout)
    if primary.checkout == mirror.checkout:
        raise SyncError("primary and mirror checkout paths must be different Git worktrees")
    plans = (primary, mirror)

    if not apply:
        for plan in plans:
            for name in plan.file_names:
                digest = sha256_path(plan.source(name))
                print(
                    f"PLAN {plan.profile}: dist/{plan.profile}/{name} -> "
                    f"{DESTINATION_DIRS[plan.profile].as_posix()}/{name} sha256={digest}"
                )
        print("DRY RUN: no files written; pass --apply to copy")
        return plans

    for plan in plans:
        plan.destination_dir.mkdir(parents=True, exist_ok=True)
        for name in plan.file_names:
            plan.destination(name).parent.mkdir(parents=True, exist_ok=True)
            _copy_exact(plan.source(name), plan.destination(name))

    for plan in plans:
        observed = assert_status_confined(plan.checkout, plan.allowed_paths)
        _assert_byte_and_hash_parity(plan)
        print(
            f"VERIFIED {plan.profile}: {len(plan.file_names)} files copied; "
            f"{len(observed)} changed paths, all confined to the exact file set"
        )
    return plans


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-checkout", required=True, type=Path, help="path to the clean primary Git checkout")
    parser.add_argument("--mirror-checkout", required=True, type=Path, help="path to the clean mirror Git checkout")
    parser.add_argument("--apply", action="store_true", help="copy files; without this flag only print a read-only plan")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        synchronize(args.primary_checkout, args.mirror_checkout, apply=args.apply)
    except SyncError as exc:
        print(f"sync rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
