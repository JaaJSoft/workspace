"""Link preview: URL extraction and OpenGraph metadata fetching."""

import re
from urllib.parse import urlparse

import httpx2
from trafilatura.metadata import extract_metadata

from workspace.ai.services.web import _HEADERS, _is_url_safe
from workspace.common.url_guard import guarded_transport, read_capped

_URL_RE = re.compile(r"https?://[^\s<>\"\')\]}]+", re.IGNORECASE)
_TRAILING_PUNCT = re.compile(r"[.,;:!?)]+$")


def extract_urls(text: str, *, max_urls: int = 5) -> list[str]:
    """Extract unique HTTP(S) URLs from text, preserving order.

    Strips trailing punctuation and deduplicates. Returns at most *max_urls*.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = _TRAILING_PUNCT.sub("", match.group(0))
        if url not in seen:
            seen.add(url)
            urls.append(url)
            if len(urls) >= max_urls:
                break
    return urls


MAX_PAGE_BYTES = 2 * 1024 * 1024


def _fetch_html(url: str) -> str:
    """Fetch a URL and return its HTML content.

    Any member of a conversation picks the URL, so every connection, redirect
    hops included, only goes to a public address, and the body is capped.
    Raises ``ValueError`` for unsafe URLs and oversized pages.
    Raises ``ValueError`` for unsafe URLs, oversized pages or HTTP errors.
    """
    if not _is_url_safe(url):
        raise ValueError("URL points to a private or internal address")

    with (
        httpx2.Client(
            transport=guarded_transport(),
            timeout=10,
            follow_redirects=True,
            headers=_HEADERS,
            max_redirects=3,
        ) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        content = read_capped(resp, MAX_PAGE_BYTES)
        encoding = resp.encoding or "utf-8"

    return content.decode(encoding, errors="replace")


def fetch_opengraph(url: str) -> dict[str, str]:
    """Fetch a URL and extract OpenGraph metadata via trafilatura.

    Returns a dict with keys: title, description, image, site_name, favicon.
    Missing keys default to empty string.
    Raises ``ValueError`` for unsafe/private URLs or HTTP errors.
    """
    html = _fetch_html(url)

    doc = extract_metadata(html, default_url=url)

    title = ""
    description = ""
    image = ""
    site_name = ""

    if doc is not None:
        title = doc.title or ""
        description = doc.description or ""
        image = doc.image or ""
        site_name = doc.sitename or ""

    # Favicon fallback: use /favicon.ico at the domain root
    parsed = urlparse(url)
    favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"

    return {
        "title": title,
        "description": description,
        "image": image,
        "site_name": site_name,
        "favicon": favicon,
    }
