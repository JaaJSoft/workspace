"""Access to the frozen compatibility corpora.

One directory per published format version, never edited after its commit:
a format change adds v2 beside v1. Each replay names the versions it opens in
a CORPORA tuple of its own and test_compat_frozen refuses a version no replay
names, so landing a second one is that tuple plus the class reading it - never
a second reader.
"""

import json
from dataclasses import dataclass
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parent / "fixtures" / "compat"


@dataclass(frozen=True)
class CorpusFiles:
    root: Path
    rows: Path
    credentials: dict
    manifest: dict
    archive: bytes


def versions() -> list[str]:
    """Every published corpus version, oldest first.

    A dot-prefixed directory is not one. The generator stages a new corpus
    beside the published ones so that publishing it is a rename on a single
    filesystem, and a walk killed outright leaves that staging directory
    behind; listing it would turn a harmless leftover into a version no
    replay covers and load() cannot open.
    """
    if not CORPUS_ROOT.is_dir():
        return []
    return sorted(
        p.name
        for p in CORPUS_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def load(version: str) -> CorpusFiles:
    root = CORPUS_ROOT / version
    return CorpusFiles(
        root=root,
        rows=root / "rows.json",
        credentials=json.loads((root / "credentials.json").read_text(encoding="utf-8")),
        manifest=json.loads((root / "manifest.json").read_text(encoding="utf-8")),
        archive=(root / "archive.bin").read_bytes(),
    )
