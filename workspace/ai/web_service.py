"""Web search and page content extraction for AI tools."""
import logging
import re
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx
import trafilatura
from django.conf import settings

logger = logging.getLogger(__name__)

# Internal/private IP ranges that must not be fetched (SSRF protection).
_BLOCKED_HOSTS = re.compile(
    r'^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|0\.0\.0\.0|::1|\[::1\])',
)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (compatible; WorkspaceBot/1.0; '
        '+https://github.com/JaaJ-Workspace)'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5,fr;q=0.3',
}


def _get_blocked_domains() -> set[str]:
    """Return the set of blocked domains from settings (cached after first call)."""
    raw = getattr(settings, 'SEARXNG_BLOCKED_DOMAINS', '')
    if not raw:
        return set()
    return {d.strip().lower() for d in raw.split(',') if d.strip()}


def _is_url_safe(url: str) -> bool:
    """Return False if *url* points to a private/internal address or blocked domain."""
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if _BLOCKED_HOSTS.search(host):
        return False
    # Check domain blocklist (matches domain and all subdomains).
    blocked = _get_blocked_domains()
    if blocked:
        for domain in blocked:
            if host == domain or host.endswith('.' + domain):
                return False
    try:
        addr = ip_address(host)
        return addr.is_global
    except ValueError:
        # Not a raw IP — allow DNS names that didn't match the blocklist.
        return True


def search(query: str, *, max_results: int = 5) -> list[dict]:
    """Search the web via SearXNG and return a list of results.

    Each result is a dict with keys: ``title``, ``url``, ``snippet``.
    Returns an empty list when SearXNG is not configured or unreachable.
    """
    base_url = getattr(settings, 'SEARXNG_URL', '')
    if not base_url:
        return []

    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(
                f'{base_url.rstrip("/")}/search',
                params={
                    'q': query,
                    'format': 'json',
                    'categories': 'general',
                    'language': 'auto',
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception('SearXNG search failed for query: %.80s', query)
        return []

    results = [
        {
            'title': r.get('title', ''),
            'url': r.get('url', ''),
            'snippet': r.get('content', ''),
        }
        for r in resp.json().get('results', [])
        if _is_url_safe(r.get('url', ''))
    ][:max_results]
    return results


def fetch_and_extract(url: str, *, max_chars: int = 6000) -> str:
    """Fetch a URL and extract its main text content.

    Uses *trafilatura* for editorial content extraction — strips navigation,
    ads, footers, and returns clean readable text.

    Raises ``ValueError`` for unsafe URLs or fetch failures.
    """
    if not _is_url_safe(url):
        raise ValueError('URL points to a private or internal address')

    try:
        with httpx.Client(
            timeout=15,
            follow_redirects=True,
            headers=_HEADERS,
            max_redirects=5,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f'Failed to fetch URL: {exc}') from exc

    # Guard against huge responses (2 MB limit).
    if len(resp.content) > 2 * 1024 * 1024:
        raise ValueError('Response too large (>2 MB)')

    text = trafilatura.extract(
        resp.text,
        include_links=False,
        include_images=False,
        include_tables=True,
    ) or ''

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
        text = ' '.join(parser.parts).strip()

    if len(text) > max_chars:
        text = text[:max_chars] + '\n\n[… truncated]'
    return text
