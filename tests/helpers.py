"""Shared standard-library helpers for repository validation."""

from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "opportunities.json"
SCHEMA_PATH = ROOT / "data" / "schema.json"
FUNDING_PULSE_SCHEMA_PATH = ROOT / "data" / "funding_pulse.schema.json"
PROFILES_PATH = ROOT / "src" / "site_profiles.json"
DIST = ROOT / "dist"
HISTORY = ROOT / "data" / "history"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_path(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class HTMLFacts(HTMLParser):
    """Collect structural, metadata, and dependency facts without extra packages."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.start_counts: dict[str, int] = {}
        self.attrs_by_tag: dict[str, list[dict[str, str]]] = {}
        self.dataset_parts: list[str] = []
        self._in_dataset = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.start_counts[tag] = self.start_counts.get(tag, 0) + 1
        self.attrs_by_tag.setdefault(tag, []).append(values)
        if tag == "script" and values.get("id") == "dataset":
            self._in_dataset = True

    def handle_endtag(self, tag):
        if tag == "script" and self._in_dataset:
            self._in_dataset = False

    def handle_data(self, data):
        if self._in_dataset:
            self.dataset_parts.append(data)

    @property
    def embedded_data(self):
        return json.loads("".join(self.dataset_parts))
