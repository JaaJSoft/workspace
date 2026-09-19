"""Access to the frozen compatibility corpora.

One directory per published format version, never edited after its commit:
a format change adds v2 beside v1. The replays import from here so that
adding a version is a directory plus a parametrised run, never a new reader.
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
    """Every published corpus version, oldest first."""
    if not CORPUS_ROOT.is_dir():
        return []
    return sorted(p.name for p in CORPUS_ROOT.iterdir() if p.is_dir())


def load(version: str) -> CorpusFiles:
    root = CORPUS_ROOT / version
    return CorpusFiles(
        root=root,
        rows=root / "rows.json",
        credentials=json.loads((root / "credentials.json").read_text(encoding="utf-8")),
        manifest=json.loads((root / "manifest.json").read_text(encoding="utf-8")),
        archive=(root / "archive.bin").read_bytes(),
    )
