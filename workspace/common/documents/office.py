"""Text extraction from zip-and-XML office documents: OOXML and OpenDocument.

One module covers both families because they are the same shape - a zip of XML
parts - and differ only in which parts hold prose and which element wraps it.
Neither can be read from a byte prefix: a zip's central directory sits at the
end of the file, so the caller has to hand over a stream it can seek.

Extraction stops at the part, and then at the element, that fills the caller's
budget. Reading a whole deck and slicing the result afterwards is the cost the
budget exists to avoid.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass

from lxml import etree

from workspace.common.logging import scrub

from .budget import TextBudget

logger = logging.getLogger(__name__)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
ODP = "application/vnd.oasis.opendocument.presentation"

# How much decompressed XML one part may spend. A zip entry declares its
# uncompressed size, but the header is written by whoever built the file, so
# the ceiling has to be enforced on the read itself - that is the difference
# between a bound and a hint. Eight megabytes of prose is around eight hundred
# pages, an order of magnitude past any budget a caller passes in, so a real
# document never reaches it and a bomb never gets past it.
MAX_PART_BYTES = 8 * 1024 * 1024

# Entities are never resolved and the DTD is never loaded: an uploaded
# document is a document from a stranger, and both are how a small one becomes
# a large one. recover=True is what lets a part truncated at MAX_PART_BYTES
# still yield the text it did contain.
_PARSER_OPTIONS = {
    "resolve_entities": False,
    "load_dtd": False,
    "no_network": True,
    "huge_tree": False,
    "recover": True,
}

_WHITESPACE_RE = re.compile(r"\s+")

_DIGITS_RE = re.compile(r"(\d+)")

# Matched on the local name, never the namespace: OOXML exists in a
# transitional and a strict flavour under different namespace URIs, and the
# three OpenDocument bodies each declare their own. The local name is the one
# thing all of them agree on.
#
# "p" is the paragraph of wordprocessingml, of the drawingml that carries the
# text of a slide, and of OpenDocument; "h" is an OpenDocument heading.
_PROSE_TAGS = frozenset({"p", "h"})

# A spreadsheet keeps its strings out of the sheets: a cell of type "s" holds
# a row number into sharedStrings.xml, so indexing its <v> would index the
# integer 4712 instead of the words it points at.
_SHARED_STRING_CELL = "s"


def _is_shared_string_index(element) -> bool:
    parent = element.getparent()
    return parent is not None and parent.get("t") == _SHARED_STRING_CELL


@dataclass(frozen=True)
class _Part:
    """Which members of the archive to read, and what to read out of them."""

    members: re.Pattern
    tags: frozenset
    skip: object = None


_SHEET_PARTS = (
    # Order is load-bearing: the shared strings are the sheet's vocabulary, so
    # they earn the budget before the cells that merely point into them.
    _Part(re.compile(r"^xl/sharedStrings\.xml$"), frozenset({"si"})),
    _Part(
        re.compile(r"^xl/worksheets/[^/]+\.xml$"),
        frozenset({"v", "is"}),
        _is_shared_string_index,
    ),
)

_CONTENT_XML = (_Part(re.compile(r"^content\.xml$"), _PROSE_TAGS),)

_RULES = {
    DOCX: (
        _Part(
            re.compile(
                r"^word/(document|footnotes|endnotes|header\d*|footer\d*)\.xml$"
            ),
            _PROSE_TAGS,
        ),
    ),
    PPTX: (_Part(re.compile(r"^ppt/(slides|notesSlides)/[^/]+\.xml$"), _PROSE_TAGS),),
    XLSX: _SHEET_PARTS,
    ODT: _CONTENT_XML,
    ODS: _CONTENT_XML,
    ODP: _CONTENT_XML,
}

SUPPORTED_MIME_TYPES = frozenset(_RULES)


def office_text(stream, mime_type: str, *, max_chars: int) -> str:
    """Prose of a zip-based office document, in document order.

    Empty when the document holds no text. Raises ValueError when *stream* is
    not a readable archive - which is also what a password-protected file
    looks like, since encrypting one wraps the zip in an OLE container.
    """
    parts = _RULES.get(mime_type)
    if parts is None:
        raise ValueError(f"Not a zip-based office document: {mime_type}")

    budget = TextBudget(max_chars)
    try:
        with zipfile.ZipFile(stream) as archive:
            names = sorted(archive.namelist(), key=_natural_key)
            for part in parts:
                for name in names:
                    if budget.full:
                        return budget.text()
                    if part.members.match(name):
                        _read_part(archive, name, part, budget)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise ValueError(f"Could not read office document: {exc}") from exc
    return budget.text()


def _natural_key(name):
    """Order slide2 ahead of slide10, the way the document does.

    Which parts a truncated read keeps is decided by this order, and the front
    of a deck says more about it than whichever slide sorts first as text.
    """
    # re.split with one capturing group alternates non-digit and digit chunks
    # from index zero, so two names always compare like against like.
    return [
        int(chunk) if chunk.isdigit() else chunk for chunk in _DIGITS_RE.split(name)
    ]


def _read_part(archive, name, part, budget):
    """Feed one archive member's text into *budget*, skipping it if unreadable.

    A single unreadable part is not worth the rest of the document: a deck
    whose fourth slide is corrupt still has thirty others to index.
    """
    try:
        with archive.open(name) as member:
            payload = member.read(MAX_PART_BYTES)
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
        logger.debug("Office part %s unreadable: %s", scrub(name), scrub(exc))
        return
    try:
        _parse_part(payload, part, budget)
    except etree.XMLSyntaxError as exc:
        logger.debug("Office part %s is not valid XML: %s", scrub(name), scrub(exc))


def _parse_part(payload, part, budget):
    for _event, element in etree.iterparse(
        io.BytesIO(payload), events=("end",), **_PARSER_OPTIONS
    ):
        if _localname(element) not in part.tags:
            continue
        if part.skip is None or not part.skip(element):
            text = _WHITESPACE_RE.sub(" ", "".join(element.itertext())).strip()
            if text:
                budget.add(text, separator="\n")
        _release(element)
        if budget.full:
            return


def _localname(element) -> str:
    """The tag without its namespace, whichever way the namespace is spelled.

    Split rather than resolved through QName, which raises on a tag whose
    prefix was never declared - and recover=True is exactly what leaves those
    in the tree. One malformed tag must not cost the whole document.
    """
    # A comment or a processing instruction has a callable for a tag.
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2].rpartition(":")[2]


def _release(element):
    """Drop the parsed tree behind *element* so a long part stays bounded.

    Clearing a consumed element also removes it from the tree its parent will
    hand to itertext() later, which is what stops a paragraph nested in
    another one from being counted twice. The tail is kept because it is the
    enclosing element's text, not this one's.
    """
    element.clear(keep_tail=True)
    ancestor = element
    while (parent := ancestor.getparent()) is not None:
        while ancestor.getprevious() is not None:
            del parent[0]
        ancestor = parent
