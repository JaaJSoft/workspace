"""RSS / Atom / RDF feed parsing for the web tools.

A feed is the cleanest navigation primitive a site offers: a dated list of
what it published, in publication order, without the homepage around it.
"""

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from lxml import etree

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

MAX_ENTRIES = 40
SUMMARY_MAX_CHARS = 220

# Entities are never resolved and the DTD is never loaded: a feed is a
# document from a stranger, and both are how a small one becomes a large one.
_PARSER = etree.XMLParser(
    resolve_entities=False,
    load_dtd=False,
    no_network=True,
    huge_tree=False,
    recover=True,
)

_RDF_ABOUT = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"
_ENTRY_TAGS = ("item", "entry")
_ROOT_TAGS = ("rss", "feed", "RDF")
_DATE_TAGS = ("pubDate", "published", "updated", "date", "created")
_SUMMARY_TAGS = ("description", "summary", "subtitle", "content")

_FEED_ROOT_RE = re.compile(r"<(rss|feed|rdf:RDF)[\s>]", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FeedEntry:
    title: str
    url: str
    date: str
    summary: str


@dataclass(frozen=True)
class Feed:
    title: str
    subtitle: str
    entries: list[FeedEntry]


def looks_like_feed(head: bytes) -> bool:
    """Guess from the first bytes of a document whether it is a feed.

    Feeds are routinely served as ``application/xml`` or ``text/xml``, which
    says nothing about what the document is.
    """
    return bool(_FEED_ROOT_RE.search(head.decode("utf-8", "replace")))


def _localname(element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _children(element, *names: str) -> list:
    return [c for c in element if _localname(c) in names]


def _child_text(element, *names: str) -> str:
    for child in _children(element, *names):
        text = " ".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _plain_text(markup: str, *, max_chars: int) -> str:
    """Reduce a summary — HTML more often than not — to one plain line."""
    text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", markup))).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _normalize_date(raw: str) -> str:
    """Reduce a feed date to ``YYYY-MM-DD``.

    RSS dates are RFC 822 and Atom dates are ISO 8601; a feed that follows
    neither keeps its own string, which still dates the entry for a reader.
    """
    raw = raw.strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except ValueError, TypeError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:40]


def _entry_url(entry, base_url: str) -> str:
    """Resolve the page an entry points at, across the three feed dialects."""
    for link in _children(entry, "link"):
        href = (link.get("href") or link.text or "").strip()
        if href and link.get("rel", "alternate") == "alternate":
            return urljoin(base_url, href)
    about = entry.get(_RDF_ABOUT)
    if about:
        return urljoin(base_url, about.strip())
    for guid in _children(entry, "guid", "id"):
        value = (guid.text or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _entry(element, base_url: str) -> FeedEntry:
    return FeedEntry(
        title=_plain_text(_child_text(element, "title"), max_chars=200),
        url=_entry_url(element, base_url),
        date=_normalize_date(_child_text(element, *_DATE_TAGS)),
        summary=_plain_text(
            _child_text(element, *_SUMMARY_TAGS), max_chars=SUMMARY_MAX_CHARS
        ),
    )


def parse_feed(
    content: bytes, base_url: str, *, max_entries: int = MAX_ENTRIES
) -> Feed | None:
    """Parse an RSS, Atom or RDF feed. Returns None when it isn't one.

    Returning None rather than raising is what lets the caller treat a
    mislabelled ``application/xml`` page as the HTML document it really is.
    """
    try:
        root = etree.fromstring(content, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        logger.debug("Feed parsing failed for %.80s: %s", scrub(base_url), scrub(exc))
        return None
    if root is None or _localname(root) not in _ROOT_TAGS:
        return None

    entries = [
        _entry(element, base_url)
        for element in root.iter()
        if _localname(element) in _ENTRY_TAGS
    ]
    if not entries:
        return None

    # RSS hangs the feed's own title off <channel>, Atom off the root, and
    # both name their entries <title> too — hence the sibling-of-entries scope.
    header = next(iter(_children(root, "channel")), root)
    return Feed(
        title=_plain_text(_child_text(header, "title"), max_chars=200),
        subtitle=_plain_text(
            _child_text(header, "description", "subtitle"), max_chars=SUMMARY_MAX_CHARS
        ),
        entries=entries[:max_entries],
    )
