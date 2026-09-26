"""Content-based file type detection using Google Magika."""

import io
import json
import os
import threading
from dataclasses import dataclass
from importlib.util import find_spec

# Located without importing magika: the package pulls in onnxruntime and
# numpy, which every process importing the name helpers below would carry.
_KB_PATH = os.path.join(
    os.path.dirname(find_spec("magika").origin), "config", "content_types_kb.min.json"
)

_magika = None
_magika_lock = threading.Lock()


def _get_magika():
    """The process's Magika model, loaded on the first content detection."""
    global _magika
    if _magika is None:
        with _magika_lock:
            if _magika is None:
                from magika import Magika

                _magika = Magika()
    return _magika


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
    result = _get_magika().identify_bytes(content)
    return DetectionResult(
        label=result.output.label,
        mime_type=result.output.mime_type,
        group=result.output.group or "",
        score=result.score,
    )


def _buffered_stream(stream):
    """The io.BufferedIOBase under *stream*'s ``.file`` wrappers, or None.

    Magika's identify_stream only accepts a buffered binary stream, and a
    Django upload wraps one twice: TemporaryUploadedFile -> tempfile wrapper
    -> BufferedRandom.
    """
    for _ in range(3):
        if isinstance(stream, io.BufferedIOBase):
            return stream
        stream = getattr(stream, "file", None)
    return stream if isinstance(stream, io.BufferedIOBase) else None


def detect_from_stream(stream) -> DetectionResult:
    # identify_stream samples the head and tail of the file; identify_bytes
    # needs the whole of it in memory, so it is the last resort.
    raw = _buffered_stream(stream)
    seekable = hasattr(stream, "seek")
    pos = stream.tell() if hasattr(stream, "tell") else 0
    if raw is not None and raw.readable():
        result = _get_magika().identify_stream(raw)
    else:
        if seekable:
            stream.seek(pos)
        result = _get_magika().identify_bytes(stream.read())
    if seekable:
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
