"""Link preview: URL extraction and OpenGraph metadata fetching."""
import re
from urllib.parse import urlparse

import httpx
from trafilatura.metadata import extract_metadata

from workspace.ai.web_service import _is_url_safe, _HEADERS

_URL_RE = re.compile(r'https?://[^\s<>\"\')\]}>]+', re.IGNORECASE)
_TRAILING_PUNCT = re.compile(r'[.,;:!?)]+$')


def extract_urls(text: str, *, max_urls: int = 5) -> list[str]:
    """Extract unique HTTP(S) URLs from text, preserving order.

    Strips trailing punctuation and deduplicates. Returns at most *max_urls*.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = _TRAILING_PUNCT.sub('', match.group(0))
        if url not in seen:
            seen.add(url)
            urls.append(url)
            if len(urls) >= max_urls:
                break
    return urls


def _fetch_html(url: str) -> str:
    """Fetch a URL and return its HTML content.

    Raises ``ValueError`` for unsafe URLs or HTTP errors.
    """
    if not _is_url_safe(url):
        raise ValueError('URL points to a private or internal address')

    with httpx.Client(
        timeout=10,
        follow_redirects=True,
        headers=_HEADERS,
        max_redirects=3,
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()

    # Guard against huge responses
    if len(resp.content) > 2 * 1024 * 1024:
        raise ValueError('Response too large (>2 MB)')

    return resp.text


def fetch_opengraph(url: str) -> dict[str, str]:
    """Fetch a URL and extract OpenGraph metadata via trafilatura.

    Returns a dict with keys: title, description, image, site_name, favicon.
    Missing keys default to empty string.
    Raises ``ValueError`` for unsafe/private URLs or HTTP errors.
    """
    html = _fetch_html(url)

    doc = extract_metadata(html, default_url=url)

    title = ''
    description = ''
    image = ''
    site_name = ''

    if doc is not None:
        title = doc.title or ''
        description = doc.description or ''
        image = doc.image or ''
        site_name = doc.sitename or ''

    # Favicon fallback: use /favicon.ico at the domain root
    parsed = urlparse(url)
    favicon = f'{parsed.scheme}://{parsed.netloc}/favicon.ico'

    return {
        'title': title,
        'description': description,
        'image': image,
        'site_name': site_name,
        'favicon': favicon,
    }
