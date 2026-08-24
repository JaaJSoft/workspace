"""Web search and page content extraction for AI tools."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address
from urllib.parse import urljoin, urlparse

import httpx2
import trafilatura
from django.conf import settings

from workspace.common.logging import scrub

from .feeds import Feed, looks_like_feed, parse_feed
from .paging import check_part, part_count
from .pdf import MAX_PAGES, PdfDocument, extract_pdf
from .reading import read_for_query

logger = logging.getLogger(__name__)

# Internal/private IP ranges that must not be fetched (SSRF protection).
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.0\.0\.0|::1|\[::1\])",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WorkspaceBot/1.0; +https://github.com/JaaJ-Workspace)"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/pdf,"
        "application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5,fr;q=0.3",
}


def _get_blocked_domains() -> set[str]:
    """Return the set of blocked domains from settings (cached after first call)."""
    raw = getattr(settings, "SEARXNG_BLOCKED_DOMAINS", "")
    if not raw:
        return set()
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _is_url_safe(url: str) -> bool:
    """Return False if *url* points to a private/internal address or blocked domain."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if _BLOCKED_HOSTS.search(host):
        return False
    # Check domain blocklist (matches domain and all subdomains).
    blocked = _get_blocked_domains()
    if blocked:
        for domain in blocked:
            if host == domain or host.endswith("." + domain):
                return False
    try:
        addr = ip_address(host)
        return addr.is_global
    except ValueError:
        # Not a raw IP — allow DNS names that didn't match the blocklist.
        return True


SEARCH_TIME_RANGES = ("day", "week", "month", "year")
SEARCH_CATEGORIES = ("general", "news", "science", "it")


def _site_domain(site: str) -> str:
    """Reduce *site* to the bare hostname a ``site:`` operator expects.

    A site is named the way it is read — with a scheme, with a path, or with
    the operator already typed in — and engines match none of those forms.
    """
    site = site.strip().lower().removeprefix("site:").strip()
    if "//" in site:
        site = site.split("//", 1)[1]
    return site.split("/", 1)[0].strip()


def search(
    query: str,
    *,
    max_results: int = 5,
    time_range: str = "",
    category: str = "general",
    site: str = "",
) -> list[dict]:
    """Search the web via SearXNG and return a list of results.

    Each result is a dict with keys: ``title``, ``url``, ``snippet``.
    *time_range* (``day``/``week``/``month``/``year``) and *category*
    (``general``/``news``/``science``/``it``) are ignored unless they name
    something SearXNG knows, so an unsupported filter widens the search rather
    than breaking it. *site* restricts the results to one domain.

    Returns an empty list when SearXNG is not configured or unreachable.
    """
    base_url = getattr(settings, "SEARXNG_URL", "")
    if not base_url:
        return []

    domain = _site_domain(site)
    if domain:
        query = f"site:{domain} {query}"

    params = {
        "q": query,
        "format": "json",
        "categories": category if category in SEARCH_CATEGORIES else "general",
        "language": "auto",
    }
    if time_range in SEARCH_TIME_RANGES:
        params["time_range"] = time_range

    try:
        with httpx2.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(
                f"{base_url.rstrip('/')}/search",
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": _HEADERS["User-Agent"],
                },
            )
            resp.raise_for_status()
    except httpx2.HTTPError:
        logger.exception("SearXNG search failed for query: %.80s", scrub(query))
        return []

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in resp.json().get("results", [])
        if _is_url_safe(r.get("url", ""))
    ][:max_results]
    return results


def search_many(queries: list[str], **kwargs) -> list[dict]:
    """Search several queries at once and merge their results.

    Each query keeps its own *max_results* budget and runs concurrently with
    the others, so a question asked from several angles costs one round of
    latency. A URL several queries return is listed once, under the first of
    them, and every result carries the ``query`` that found it. A lone query
    returns the plain :func:`search` shape.
    """
    if not queries:
        return []
    if len(queries) == 1:
        return search(queries[0], **kwargs)

    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        batches = pool.map(lambda q: search(q, **kwargs), queries)

    seen: set[str] = set()
    merged = []
    for query, batch in zip(queries, batches, strict=True):
        for result in batch:
            if result["url"] in seen:
                continue
            seen.add(result["url"])
            merged.append({**result, "query": query})
    return merged


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# A PDF carries its fonts and images along with its text, so the budget that
# fits a whole website's HTML barely fits a ten-page paper.
MAX_PDF_BYTES = 10 * 1024 * 1024

MAX_LINKS = 25
ANCHOR_MAX_CHARS = 80
# Room a part keeps aside for the marker naming the part that follows it. Set
# rather than measured, so a part covers the same characters whatever the
# numbers written into that marker cost.
PART_MARKER_CHARS = 140
# Head of the document the tag-stripping fallback parses. Markup outruns text
# by an order of magnitude, so this still yields far more than any max_chars.
FALLBACK_PARSE_CHARS = 200_000

_FEED_CONTENT_TYPES = ("rss+xml", "atom+xml", "rdf+xml", "feed+json")

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_WHITESPACE_RE = re.compile(r"\s+")


def _absolutize_links(markdown: str, base_url: str) -> str:
    """Rewrite relative markdown link targets against *base_url*.

    Extraction preserves hrefs exactly as the page wrote them, and most sites
    write them relative — a link the reader cannot fetch is no link at all.
    """
    return _MD_LINK_RE.sub(lambda m: f"[{m[1]}]({urljoin(base_url, m[2])})", markdown)


def _truncate(text: str, max_chars: int) -> str:
    """Cap *text* at *max_chars*, marker included.

    The marker counts against the budget: the caller asked for a page that
    fits in *max_chars*, and a cap that quietly returns more is not a cap.
    """
    if len(text) <= max_chars:
        return text
    marker = f"\n\n[… truncated at {max_chars} characters — the page continues]"
    return text[: max(max_chars - len(marker), 0)] + marker


def _header(title: str, final_url: str, *fields: str) -> str:
    """Build the block that opens every result.

    The URL is the one the redirects landed on: it is what a citation has to
    name, and the bot is otherwise never told the two differ.
    """
    lines = [f"# {title}"] if title else []
    lines.append(f"Source: {final_url}")
    lines.extend(field for field in fields if field)
    return "\n".join(lines)


def _bare_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _collect_links(markdown: str, final_url: str) -> tuple[list, list]:
    """Split the links of an extracted page into same-site and outbound ones.

    Going deeper into a site and leaving it for another source are different
    decisions, and a bot choosing its next fetch makes them separately.
    """
    base_host = _bare_host(final_url)
    seen = {final_url.split("#", 1)[0]}
    same: list[tuple[str, str]] = []
    external: list[tuple[str, str]] = []

    for match in _MD_LINK_RE.finditer(markdown):
        url = match[2].split("#", 1)[0]
        anchor = _WHITESPACE_RE.sub(" ", match[1]).strip()
        # Anchors of one or two characters are pagination and breadcrumb
        # arrows — navigation chrome that survived extraction.
        if (
            not url.startswith(("http://", "https://"))
            or url in seen
            or len(anchor) < 3
        ):
            continue
        seen.add(url)
        if len(anchor) > ANCHOR_MAX_CHARS:
            anchor = anchor[: ANCHOR_MAX_CHARS - 1].rstrip() + "…"
        target = same if _bare_host(url) == base_host else external
        target.append((anchor, url))

    return same, external


def _render_links(same: list, external: list, *, max_links: int = MAX_LINKS) -> str:
    """Render the link list, splitting the cap between the two groups."""
    if not same and not external:
        return ""

    half = max_links // 2
    keep_same = min(len(same), max(half, max_links - len(external)))
    keep_external = min(len(external), max_links - keep_same)
    dropped = (len(same) - keep_same) + (len(external) - keep_external)

    lines = ["## Links"]
    for label, links, keep in (
        ("On this page's site", same, keep_same),
        ("Elsewhere", external, keep_external),
    ):
        if not keep:
            continue
        lines.append(f"{label}:")
        lines.extend(f"- [{anchor}]({url})" for anchor, url in links[:keep])
    if dropped:
        lines.append(f"({dropped} more links not listed)")
    return "\n".join(lines)


def _paged(text: str, max_chars: int, part: int) -> str:
    """Return the *part*-th slice of *text*, ending on how to read the next one.

    The marker is where the reader learns the rest is within reach at all: a
    page cut with nothing said about the cut is a page that ends there.
    """
    if len(text) <= max_chars:
        check_part(part, 1)
        return text

    size = max(max_chars - PART_MARKER_CHARS, max_chars // 2, 1)
    total = part_count(len(text), size)
    check_part(part, total)

    stretch = text[(part - 1) * size : part * size]
    if part == total:
        return f"{stretch}\n\n[… end of the page — part {part} of {total}]"
    return (
        f"{stretch}\n\n[… part {part} of {total} — read this URL again with "
        f"part={part + 1} for what follows]"
    )


def _one_part(body: str, query: str, max_chars: int, part: int) -> str:
    """Cut *body* down to the one part of it this call returns.

    With a query that is what the stretch of the page the part covers says
    about it; without one, that stretch itself.
    """
    if not body:
        return ""
    if query.strip():
        return read_for_query(body, query, max_chars=max_chars, part=part)
    return _paged(body, max_chars, part)


def _body_budget(header: str, links: str, max_chars: int) -> tuple[int, str]:
    """Room left for the body, and the link list that still fits beside it.

    The body is what was asked for: the link list is dropped whole rather than
    allowed to eat into the page it belongs to. Read before the body is built
    as well as while it is composed, so a body assembled for a question aims
    at the room it will actually get.
    """
    budget = max_chars - len(header) - len(links) - 4
    if budget < max_chars // 2:
        return max_chars - len(header) - 2, ""
    return budget, links


def _compose(header: str, body: str, links: str, max_chars: int) -> str:
    """Assemble header, body and link list within a single *max_chars* budget."""
    body_budget, links = _body_budget(header, links, max_chars)

    body = _truncate(body, max(body_budget, 0)) if body else ""
    return _truncate(
        "\n\n".join(part for part in (header, body, links) if part), max_chars
    )


def _json_or_none(resp) -> str | None:
    """Re-serialize a JSON response compactly, or return None if it isn't JSON.

    HTML extraction yields nothing on a JSON document, and a pretty-printed
    payload spends most of the character budget on indentation.
    """
    if "json" not in resp.headers.get("content-type", "").lower():
        return None
    try:
        return json.dumps(resp.json(), ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        return None


def _is_pdf(content_type: str, content: bytes) -> bool:
    return "application/pdf" in content_type or content.startswith(b"%PDF-")


def _is_feed(content_type: str, content: bytes) -> bool:
    if any(feed_type in content_type for feed_type in _FEED_CONTENT_TYPES):
        return True
    # Feeds are routinely served as application/xml, text/xml or even
    # text/plain, so the document's own root element is the reliable signal.
    return looks_like_feed(content[:400])


def _render_pdf(
    document: PdfDocument,
    final_url: str,
    max_chars: int,
    query: str = "",
    part: int = 1,
) -> str:
    pages = f"Pages: {document.page_count}"
    if document.pages_read < document.page_count:
        pages += f" (read the first {document.pages_read})"
    header = _header(
        document.title,
        final_url,
        f"Published: {document.date}" if document.date else "",
        pages,
    )
    body = document.text or (
        "This PDF carries no text layer — it is a scan or a set of page "
        "images, and no reader can extract words from it. Look for an HTML "
        "version of the same document instead."
    )
    budget, _ = _body_budget(header, "", max_chars)
    return _compose(header, _one_part(body, query, budget, part), "", max_chars)


def _render_feed(
    feed: Feed, final_url: str, max_chars: int, query: str = "", part: int = 1
) -> str:
    """Render a feed as a dated list — the entries are the point, not prose."""
    header = _header(
        feed.title,
        final_url,
        feed.subtitle,
        f"Entries: {len(feed.entries)}, newest first as the feed ordered them",
    )
    lines = []
    for entry in feed.entries:
        date = f"{entry.date} — " if entry.date else ""
        title = entry.title or entry.url or "(untitled)"
        lines.append(
            f"- {date}[{title}]({entry.url})" if entry.url else f"- {date}{title}"
        )
        if entry.summary:
            lines.append(f"  {entry.summary}")
    budget, _ = _body_budget(header, "", max_chars)
    return _compose(
        header, _one_part("\n".join(lines), query, budget, part), "", max_chars
    )


def _page_metadata(html: str, final_url: str) -> tuple[str, str, str]:
    """Return the title, publication date and author trafilatura can find.

    The date is the field that makes a source datable, and a bot that cannot
    tell a 2019 article from last week's cites both with the same confidence.
    """
    try:
        metadata = trafilatura.extract_metadata(html, default_url=final_url)
    except Exception as exc:
        logger.debug(
            "Metadata extraction failed for %s: %s", scrub(final_url), scrub(exc)
        )
        return "", "", ""
    if metadata is None:
        return "", "", ""
    return (
        (metadata.title or "").strip(),
        (metadata.date or "").strip(),
        (metadata.author or "").strip(),
    )


def _strip_tags(html: str) -> str:
    """Last-resort extraction: the page's text, tags removed.

    Only the head of the document is parsed: past that point every remaining
    character competes for a budget the visible text has already used up.
    """
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data):
            self.parts.append(data)

    parser = _TextExtractor()
    parser.feed(html[:FALLBACK_PARSE_CHARS])
    return " ".join(parser.parts).strip()


def _render_html(
    page: str, final_url: str, max_chars: int, query: str = "", part: int = 1
) -> str:
    # Links are kept: they are how a reader moves from this page to the ones
    # it references, and a text-only extraction leaves no way back. They
    # resolve against the URL the redirects landed on, not the one asked for.
    text = _absolutize_links(
        trafilatura.extract(
            page,
            include_links=True,
            include_images=False,
            include_tables=True,
            output_format="markdown",
            url=final_url,
        )
        or "",
        final_url,
    )
    if not text:
        text = _strip_tags(page)
    # The link list is gathered before the query narrows the body: it is the
    # page's map, and a reader scoped to one section still navigates the rest.
    links = _render_links(*_collect_links(text, final_url))

    title, date, author = _page_metadata(page, final_url)
    header = _header(
        title,
        final_url,
        f"Published: {date}" if date else "",
        f"By: {author}" if author else "",
    )
    budget, _ = _body_budget(header, links, max_chars)
    return _compose(header, _one_part(text, query, budget, part), links, max_chars)


def fetch_and_extract(
    url: str, *, max_chars: int = 12000, query: str = "", part: int = 1
) -> str:
    """Fetch a URL and extract its content as link-preserving markdown.

    Four kinds of document are read: HTML through *trafilatura* (editorial
    content, navigation and ads stripped, hyperlinks kept so a follow-up fetch
    can navigate), PDF through *pypdf*, RSS/Atom feeds as a dated entry list,
    and JSON as compact JSON. Everything but JSON opens with a header naming
    the page's title, date and final URL, and HTML closes with its links
    gathered into a list.

    A *query* narrows what comes back: a page too long for the budget is
    reduced to the passages of it that answer the query, plus the outline of
    its sections — JSON excepted, a compact payload having no passages to
    choose between.

    A document still too long for one result comes back cut into parts, each
    of them naming the part that follows it; *part* asks for that one. The
    budget is then per call rather than per page, and no stretch of a document
    is out of reach.

    Raises ``ValueError`` for unsafe URLs, fetch failures, and a *part* the
    document does not have.
    """
    if not _is_url_safe(url):
        raise ValueError("URL points to a private or internal address")

    try:
        with httpx2.Client(
            timeout=15,
            follow_redirects=True,
            headers=_HEADERS,
            max_redirects=5,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx2.HTTPError as exc:
        raise ValueError(f"Failed to fetch URL: {exc}") from exc

    content = resp.content
    final_url = str(resp.url)
    content_type = resp.headers.get("content-type", "").lower()

    if _is_pdf(content_type, content):
        if len(content) > MAX_PDF_BYTES:
            raise ValueError(f"PDF too large (>{MAX_PDF_BYTES // (1024 * 1024)} MB)")
        return _render_pdf(
            extract_pdf(content, max_pages=MAX_PAGES), final_url, max_chars, query, part
        )

    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError(
            f"Response too large (>{MAX_RESPONSE_BYTES // (1024 * 1024)} MB)"
        )

    payload = _json_or_none(resp)
    if payload is not None:
        return _paged(payload, max_chars, part)

    if _is_feed(content_type, content):
        feed = parse_feed(content, final_url)
        if feed is not None:
            return _render_feed(feed, final_url, max_chars, query, part)

    return _render_html(resp.text, final_url, max_chars, query, part)
