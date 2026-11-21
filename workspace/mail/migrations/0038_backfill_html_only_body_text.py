"""Give HTML-only messages a text body, and with it a preview.

``body_text`` and ``snippet`` were only filled from a text/plain part, so a
message sent as HTML alone had no preview, could not be found by the words of
its body and quoted nothing on reply. The parser now derives them from the
HTML; this fills in the messages stored before.

The conversion is a copy of ``services/html_text.py`` as it stood when this
migration was written: a migration is a historical record and must not change
meaning when the live code does. Updating the rows fires the full-text index
triggers, so the bodies become searchable with no reindex.

Batched so the write transaction stays short on SQLite (single writer).
"""

import re
from itertools import batched

from django.db import migrations
from lxml import etree
from lxml import html as lxml_html

BATCH_SIZE = 500

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


def _html_to_text(html):
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


def backfill(apps, schema_editor):
    MailMessage = apps.get_model("mail", "MailMessage")
    db = schema_editor.connection.alias
    rows = (
        MailMessage.objects.using(db)
        .filter(body_text="")
        .exclude(body_html="")
        .only("uuid", "body_html", "body_text", "snippet")
        .iterator(chunk_size=BATCH_SIZE)
    )
    for chunk in batched(rows, BATCH_SIZE, strict=False):
        changed = []
        for message in chunk:
            message.body_text = _html_to_text(message.body_html)
            if not message.body_text:
                continue
            if not message.snippet:
                message.snippet = message.body_text[:300].replace("\n", " ").strip()
            changed.append(message)
        if changed:
            MailMessage.objects.using(db).bulk_update(
                changed, ["body_text", "snippet"]
            )


class Migration(migrations.Migration):
    # Commit per batch instead of one long transaction: keeps the SQLite
    # write lock short, and the backfill is idempotent if interrupted.
    atomic = False

    dependencies = [
        ("mail", "0037_trusted_sender"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
