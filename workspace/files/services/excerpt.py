"""Extract a short preview (first real content line) from a text file.

Used by the note card popover to show a one-line excerpt under the title.
``first_line_from_text`` is pure (string in, string out); ``first_content_line``
reads the file's stored content (bounded) and delegates to it.
"""

from __future__ import annotations

import re

from ._content import read_text_content

# Notes are short; 8 KB is plenty to find the first real line without slurping
# a large blob on every hover.
_EXCERPT_SCAN_MAX_BYTES = 8192

_HEADING_RE = re.compile(r"^#{1,6}(\s|$)")
_LIST_OR_QUOTE_RE = re.compile(r"^\s*([-*+>]|\d+\.)\s+")
_EMPHASIS_RE = re.compile(r"[*_`]")


def _strip_frontmatter(lines):
    """Drop a leading YAML frontmatter block (``---`` ... ``---``)."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1 :]
        # Unterminated frontmatter: nothing usable after it.
        return []
    return lines


def first_line_from_text(text, max_len=160) -> str:
    """Return the first real content line from markdown/plain text.

    Skips a leading YAML frontmatter block, markdown headings, and blank
    lines. Strips leading list/quote markers and inline emphasis/code markers.
    Returns ``""`` when there is no real line. Truncates to ``max_len``.
    """
    if not text:
        return ""
    lines = _strip_frontmatter(text.splitlines())
    for raw in lines:
        line = raw.strip()
        if not line or _HEADING_RE.match(line):
            continue
        line = _LIST_OR_QUOTE_RE.sub("", line, count=1).strip()
        line = _EMPHASIS_RE.sub("", line).strip()
        if not line:
            continue
        return line[:max_len]
    return ""


def first_content_line(file_obj, max_len=160) -> str:
    """Read ``file_obj``'s text (bounded) and return its first real line.

    Returns ``""`` for folders, empty files, or content that cannot be read
    or decoded as text.
    """
    text = read_text_content(file_obj, max_bytes=_EXCERPT_SCAN_MAX_BYTES)
    if not text:
        return ""
    return first_line_from_text(text, max_len=max_len)
