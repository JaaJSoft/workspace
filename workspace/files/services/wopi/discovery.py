"""WOPI discovery: which editor actions exist for which extension.

Every WOPI client (Collabora, OnlyOffice, Office Online Server) publishes an
XML document at ``/hosting/discovery`` listing, per file extension, the
actions it supports (``view``, ``edit``, ...) and the iframe URL template
(``urlsrc``) to load them. Parsing that document is the only editor-specific
knowledge in the integration - everything else is the protocol.
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx2
from django.conf import settings
from django.core.cache import cache

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

_CACHE_TTL = 3600
_FAILURE_TTL = 60
_FAILURE_MARKER = "unavailable"

# ``urlsrc`` embeds optional-parameter placeholders such as ``<ui=UI_LLCC&>``;
# hosts that don't use them must strip them before appending WOPISrc.
_PLACEHOLDER_RE = re.compile(r"<[^>]*>")


def _cache_key() -> str:
    url_digest = hashlib.sha256(settings.WOPI_DISCOVERY_URL.encode()).hexdigest()[:16]
    return f"files:wopi:discovery:{url_digest}"


def _parse(xml_text: str) -> dict:
    """Map ``{extension: {action_name: urlsrc}}`` from a discovery document."""
    actions = {}
    root = ET.fromstring(xml_text)
    for action in root.iter("action"):
        ext = (action.get("ext") or "").lower()
        name = action.get("name") or ""
        urlsrc = action.get("urlsrc") or ""
        if not ext or not name or not urlsrc:
            continue
        actions.setdefault(ext, {})[name] = urlsrc
    return actions


def get_actions() -> dict | None:
    """Discovery map for the configured editor, or None when unavailable.

    Cached for an hour; a fetch/parse failure is cached briefly too, so an
    editor being down doesn't add a network round-trip to every folder render.
    """
    if not settings.WOPI_DISCOVERY_URL:
        return None
    key = _cache_key()
    cached = cache.get(key)
    if cached == _FAILURE_MARKER:
        return None
    if cached is not None:
        return cached
    try:
        with httpx2.Client(timeout=10, follow_redirects=True) as client:
            response = client.get(settings.WOPI_DISCOVERY_URL)
            response.raise_for_status()
        actions = _parse(response.text)
    except httpx2.HTTPError, ET.ParseError:
        logger.warning(
            "WOPI discovery fetch failed for %s", scrub(settings.WOPI_DISCOVERY_URL)
        )
        cache.set(key, _FAILURE_MARKER, _FAILURE_TTL)
        return None
    cache.set(key, actions, _CACHE_TTL)
    return actions


def get_action_url(extension: str, action: str) -> str | None:
    """Placeholder-free ``urlsrc`` for *action* on *extension*, or None.

    A missing ``view`` action falls back to ``edit``: CheckFileInfo's
    ``UserCanWrite: false`` is what enforces read-only, so handing a view-only
    user the edit frame is safe - the editor renders it without editing UI.
    """
    actions = get_actions()
    if not actions:
        return None
    by_action = actions.get((extension or "").lower())
    if not by_action:
        return None
    urlsrc = by_action.get(action)
    if urlsrc is None and action == "view":
        urlsrc = by_action.get("edit")
    if urlsrc is None:
        return None
    return _PLACEHOLDER_RE.sub("", urlsrc)


def build_editor_url(action_url: str, wopi_src: str) -> str:
    """Append the WOPISrc parameter to a placeholder-free action URL."""
    if action_url.endswith("?") or action_url.endswith("&"):
        separator = ""
    elif "?" in action_url:
        separator = "&"
    else:
        separator = "?"
    return f"{action_url}{separator}WOPISrc={quote(wopi_src, safe='')}"
