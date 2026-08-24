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

logger = logging.getLogger(__name__)

# Internal/private IP ranges that must not be fetched (SSRF protection).
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.0\.0\.0|::1|\[::1\])",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WorkspaceBot/1.0; +https://github.com/JaaJ-Workspace)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


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


def fetch_and_extract(url: str, *, max_chars: int = 12000) -> str:
    """Fetch a URL and extract its main content as link-preserving markdown.

    Uses *trafilatura* for editorial content extraction — strips navigation,
    ads and footers, but keeps hyperlinks so a follow-up fetch can navigate to
    the pages this one references. JSON responses are returned as compact JSON.

    Raises ``ValueError`` for unsafe URLs or fetch failures.
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

    if len(resp.content) > 2 * 1024 * 1024:
        raise ValueError("Response too large (>2 MB)")

    payload = _json_or_none(resp)
    if payload is not None:
        return _truncate(payload, max_chars)

    # Links are kept: they are how a reader moves from this page to the ones
    # it references, and a text-only extraction leaves no way back. They
    # resolve against the URL the redirects landed on, not the one asked for.
    final_url = str(resp.url)
    text = (
        trafilatura.extract(
            resp.text,
            include_links=True,
            include_images=False,
            include_tables=True,
            output_format="markdown",
            url=final_url,
        )
        or ""
    )
    text = _absolutize_links(text, final_url)

    if not text:
        # Fallback: grab raw text stripped of tags.
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data):
                self.parts.append(data)

        parser = _TextExtractor()
        parser.feed(resp.text[:200_000])
        text = " ".join(parser.parts).strip()

    return _truncate(text, max_chars)
