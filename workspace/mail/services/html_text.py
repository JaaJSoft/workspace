"""Plain text of an HTML body, for a message that carries no text part.

The list preview, full-text search, the reply quote and the assistant all read
``body_text``. A message sent as HTML only would leave every one of them empty.
"""

import re

from lxml import etree
from lxml import html as lxml_html

# Elements that stand on lines of their own, and those that are also set off
# from their neighbours by a blank line.
_LINE_TAGS = {
    "div",
    "li",
    "dt",
    "dd",
    "tr",
    "table",
    "ul",
    "ol",
    "dl",
    "thead",
    "tbody",
    "tfoot",
    "hr",
}
_PARAGRAPH_TAGS = {"p", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}
_CELL_TAGS = {"td", "th"}

_SPACES = re.compile(r"\s+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _TextBuilder:
    def __init__(self):
        self.parts = []

    def _at_line_start(self):
        return not self.parts or self.parts[-1].endswith("\n")

    def text(self, value, preformatted):
        if not value:
            return
        if preformatted:
            self.parts.append(value)
            return
        value = _SPACES.sub(" ", value)
        if self._at_line_start():
            value = value.lstrip()
        elif self.parts[-1].endswith(" ") and value.startswith(" "):
            value = value[1:]
        if value:
            self.parts.append(value)

    def line_break(self):
        self._end_line()
        self.parts.append("\n")

    def newlines(self, count):
        """End the line, leaving at least ``count`` newlines, not ``count`` more:
        a block closing next to one opening makes one break, not two."""
        if not self.parts:
            return
        self._end_line()
        present = 0
        for part in reversed(self.parts):
            text = part.rstrip("\n")
            present += len(part) - len(text)
            if text or present >= count:
                break
        if present < count:
            self.parts.append("\n" * (count - present))

    def _end_line(self):
        if self.parts:
            self.parts[-1] = self.parts[-1].rstrip(" ")

    def result(self):
        return _BLANK_LINES.sub("\n\n", "".join(self.parts)).strip()


def _breaks(tag):
    if tag in _PARAGRAPH_TAGS:
        return 2
    if tag in _LINE_TAGS:
        return 1
    return 0


def html_to_text(html):
    """The readable text of ``html``: one line per block, links as their text."""
    if not html or not html.strip():
        return ""
    try:
        root = lxml_html.fragment_fromstring(html, create_parent="div")
    except etree.ParserError:
        return ""
    builder = _TextBuilder()
    preformatted = 0
    # iterwalk rather than recursion: a hostile body can nest deeper than the
    # interpreter's stack.
    for event, element in etree.iterwalk(root, events=("start", "end")):
        tag = element.tag if isinstance(element.tag, str) else ""
        if event == "start":
            if tag == "br":
                builder.line_break()
            elif breaks := _breaks(tag):
                builder.newlines(breaks)
            if tag == "li":
                builder.text("- ", preformatted=True)
            if tag == "pre":
                preformatted += 1
            if tag:
                builder.text(element.text, preformatted)
            continue
        if tag == "pre":
            preformatted -= 1
        if breaks := _breaks(tag):
            builder.newlines(breaks)
        elif tag in _CELL_TAGS:
            builder.text(" ", preformatted=False)
        if element is not root:
            builder.text(element.tail, preformatted)
    return builder.result()
