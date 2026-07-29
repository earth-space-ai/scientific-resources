from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import DIST, ROOT

sys.path.insert(0, str(ROOT / "src"))
import sync_checkouts  # noqa: E402


class SyncCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.scratch = ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.temporary.name)
        self.primary = self._make_checkout("primary-checkout")
        self.mirror = self._make_checkout("mirror-checkout")

    def tearDown(self):
        self.temporary.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def _git(self, checkout: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _make_checkout(self, name: str) -> Path:
        checkout = self.root / name
        checkout.mkdir()
        commands = [
            ("init", "--quiet"),
            ("config", "user.name", "Exact Sync Test"),
            ("config", "user.email", "sync-test@example.invalid"),
        ]
        for command in commands:
            result = self._git(checkout, *command)
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        (checkout / "README.md").write_text("destination checkout\n", encoding="utf-8")
        (checkout / "next.config.mjs").write_text("// committed host configuration\n", encoding="utf-8")
        result = self._git(checkout, "add", "README.md", "next.config.mjs")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        result = self._git(checkout, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "initial")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        return checkout

    def _run_helper(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "src" / "sync_checkouts.py"),
                "--primary-checkout",
                str(self.primary),
                "--mirror-checkout",
                str(self.mirror),
                *extra,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_default_dry_run_is_read_only(self):
        before = {
            checkout: (checkout / "next.config.mjs").read_bytes()
            for checkout in (self.primary, self.mirror)
        }
        result = self._run_helper()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("DRY RUN: no files written", result.stdout)
        expected_plan_count = sum(len(sync_checkouts.generated_file_names(profile)) for profile in ("primary", "mirror"))
        self.assertEqual(result.stdout.count("PLAN "), expected_plan_count)
        self.assertFalse((self.primary / "public" / "scientific-resources").exists())
        self.assertFalse((self.mirror / "scientific-resources").exists())
        for checkout in (self.primary, self.mirror):
            self.assertEqual(sync_checkouts.git_status_paths(checkout), frozenset())
            self.assertEqual((checkout / "next.config.mjs").read_bytes(), before[checkout])

    def test_apply_copies_exact_sets_and_preserves_host_configuration(self):
        next_config_before = (self.primary / "next.config.mjs").read_bytes()
        result = self._run_helper("--apply")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("VERIFIED "), 2)
        expected = {
            "primary": (self.primary, Path("public/scientific-resources")),
            "mirror": (self.mirror, Path("scientific-resources")),
        }
        for profile, (checkout, relative_dir) in expected.items():
            file_names = sync_checkouts.generated_file_names(profile)
            expected_status = frozenset((relative_dir / name).as_posix() for name in file_names)
            self.assertEqual(sync_checkouts.git_status_paths(checkout), expected_status)
            destination = checkout / relative_dir
            self.assertTrue((destination / "snapshots" / "index.json").is_file())
            for name in file_names:
                self.assertEqual((destination / name).read_bytes(), (DIST / profile / name).read_bytes())
        self.assertEqual((self.primary / "next.config.mjs").read_bytes(), next_config_before)

    def test_primary_and_mirror_use_their_correct_build_profiles(self):
        result = self._run_helper("--apply")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        primary_destination = self.primary / "public" / "scientific-resources"
        mirror_destination = self.mirror / "scientific-resources"
        self.assertEqual((primary_destination / "index.html").read_bytes(), (DIST / "primary" / "index.html").read_bytes())
        self.assertEqual((mirror_destination / "index.html").read_bytes(), (DIST / "mirror" / "index.html").read_bytes())
        self.assertNotEqual((primary_destination / "index.html").read_bytes(), (DIST / "mirror" / "index.html").read_bytes())
        self.assertNotEqual((mirror_destination / "index.html").read_bytes(), (DIST / "primary" / "index.html").read_bytes())
        primary_manifest = json.loads((primary_destination / "provenance.json").read_text(encoding="utf-8"))
        mirror_manifest = json.loads((mirror_destination / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(primary_manifest["profile"]["id"], "primary")
        self.assertEqual(mirror_manifest["profile"]["id"], "mirror")

    def test_post_apply_collateral_path_is_rejected(self):
        original_copy = sync_checkouts._copy_exact
        introduced = False

        def copy_with_collateral(source: Path, destination: Path) -> None:
            nonlocal introduced
            original_copy(source, destination)
            if not introduced:
                (self.primary / "README.md").write_text("collateral modification\n", encoding="utf-8")
                introduced = True

        with mock.patch.object(sync_checkouts, "_copy_exact", side_effect=copy_with_collateral):
            with self.assertRaisesRegex(sync_checkouts.SyncError, "outside allowed file set"):
                sync_checkouts.synchronize(self.primary, self.mirror, apply=True)

    def test_dirty_pre_state_is_rejected_before_any_copy(self):
        (self.mirror / "README.md").write_text("dirty before sync\n", encoding="utf-8")
        with self.assertRaisesRegex(sync_checkouts.SyncError, "dirty pre-state rejected"):
            sync_checkouts.synchronize(self.primary, self.mirror, apply=True)
        self.assertFalse((self.primary / "public" / "scientific-resources").exists())
        self.assertFalse((self.mirror / "scientific-resources").exists())


if __name__ == "__main__":
    unittest.main()
