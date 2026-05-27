"""Content-based file type detection using Google Magika."""

import json
import os
from dataclasses import dataclass

import magika as magika_pkg
from magika import Magika

_magika = Magika()

_KB_PATH = os.path.join(
    os.path.dirname(magika_pkg.__file__), "config", "content_types_kb.min.json"
)


def _load_kb():
    with open(_KB_PATH) as f:
        return json.load(f)


def _build_extension_map(kb):
    mapping = {}
    for label, info in kb.items():
        for ext in info.get("extensions", []):
            dot_ext = f".{ext}" if not ext.startswith(".") else ext
            mapping.setdefault(dot_ext.lower(), label)
    return mapping


_KB = _load_kb()
_EXT_TO_LABEL = _build_extension_map(_KB)


@dataclass(frozen=True)
class DetectionResult:
    label: str
    mime_type: str
    group: str
    score: float


def detect_from_bytes(content: bytes) -> DetectionResult:
    result = _magika.identify_bytes(content)
    return DetectionResult(
        label=result.output.label,
        mime_type=result.output.mime_type,
        group=result.output.group or "",
        score=result.score,
    )


def detect_from_stream(stream) -> DetectionResult:
    # Magika's identify_stream performs a strict isinstance(stream, BinaryIO)
    # check that rejects Django file wrappers (ContentFile,
    # TemporaryUploadedFile, etc.).  Try the raw inner stream first; if that
    # still fails, fall back to reading bytes and restoring the position.
    raw = getattr(stream, "file", stream)
    pos = stream.tell() if hasattr(stream, "tell") else 0
    try:
        result = _magika.identify_stream(raw)
    except TypeError:
        stream.seek(pos)
        data = stream.read()
        stream.seek(pos)
        result = _magika.identify_bytes(data)
    else:
        if hasattr(stream, "seek"):
            stream.seek(pos)
    return DetectionResult(
        label=result.output.label,
        mime_type=result.output.mime_type,
        group=result.output.group or "",
        score=result.score,
    )


def detect_from_name(filename: str) -> DetectionResult:
    """Guess file type from filename extension when content is unavailable."""
    if not filename:
        return DetectionResult(
            label="unknown", mime_type="application/octet-stream", group="", score=0.0
        )

    _, ext = os.path.splitext(filename)
    label = _EXT_TO_LABEL.get(ext.lower(), "unknown")
    info = _KB.get(label, {})
    return DetectionResult(
        label=label,
        mime_type=info.get("mime_type", "application/octet-stream"),
        group=info.get("group", "") or "",
        score=1.0 if label != "unknown" else 0.0,
    )


def get_label_info(label: str) -> dict:
    """Return raw KB entry for a label. Used by filetype registry."""
    return _KB.get(label, {})


def get_all_labels() -> dict:
    """Return the full Magika KB dict. Used by filetype registry at init."""
    return _KB
