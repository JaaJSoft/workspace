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
        if hasattr(stream, "seek"):
            stream.seek(pos)
        data = stream.read()
        if hasattr(stream, "seek"):
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


def label_from_name(filename: str) -> str:
    """Return the Magika label implied by a filename's extension, or 'unknown'.

    Extension-only lookup, no content inspection. Used as a supplementary hint
    when content detection yields a generic label (e.g. ``txt`` for a sparse
    Markdown file whose ``.md`` extension reveals the real intent).
    """
    if not filename:
        return "unknown"
    _, ext = os.path.splitext(filename)
    return _EXT_TO_LABEL.get(ext.lower(), "unknown")


# Labels Magika emits when the content alone is inconclusive. For these, the
# filename extension is a better signal of the author's intent.
_GENERIC_CONTENT_LABELS = frozenset({"txt", "unknown", "empty"})


def refine_with_name(label: str, filename: str) -> str:
    """Refine an inconclusive content label using the filename extension.

    Magika classifies a sparse Markdown file (e.g. ``# Title``) as ``txt``; its
    ``.md`` extension reveals the real type. Only the generic labels in
    ``_GENERIC_CONTENT_LABELS`` are refined, and only toward another text-group
    label, so a confidently detected binary -- or a text blob misnamed
    ``.png`` -- is never rewritten.
    """
    if label not in _GENERIC_CONTENT_LABELS:
        return label
    ext_label = label_from_name(filename)
    if ext_label in _GENERIC_CONTENT_LABELS or ext_label == label:
        return label
    if _KB.get(ext_label, {}).get("group") != "text":
        return label
    return ext_label


def has_extension(filename: str) -> bool:
    """True if the filename carries a non-empty extension.

    Dotfiles like ``.gitignore`` count as having no extension (matching
    ``os.path.splitext``), which is the intended behaviour: a viewer that
    needs an explicit extension should not claim them.
    """
    if not filename:
        return False
    return bool(os.path.splitext(filename)[1])


def detect_from_name(filename: str) -> DetectionResult:
    """Guess file type from filename extension when content is unavailable."""
    if not filename:
        return DetectionResult(
            label="unknown", mime_type="application/octet-stream", group="", score=0.0
        )

    label = label_from_name(filename)
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
