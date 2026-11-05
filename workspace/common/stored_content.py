"""Headers for a response that carries a stored file's bytes.

A file served from the app's own origin with the MIME type it was stored with
is rendered by a browser that opens its URL directly. An HTML, XHTML, SVG or
XML file then becomes a page of the app, and its scripts run with the viewer's
session. Two independent layers prevent that, so neither relies on the other
(a reverse proxy rewriting the Content-Security-Policy, a client that ignores
it):

- a type the browser turns into a scriptable document is always sent as an
  attachment (``is_active_content``);
- every other response is sandboxed by a policy that also covers types nobody
  thought to list (``stored_content_headers``).
"""

# No allow-scripts and no allow-same-origin: the document runs no script and
# gets an opaque origin, so it cannot read the app's cookies or call its API.
STORED_CONTENT_CSP = "default-src 'none'; sandbox"

_ACTIVE_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
        "text/xsl",
    }
)


def _essences(content_type):
    """Every MIME type a browser may read off the Content-Type *content_type*.

    The Fetch standard splits the header on commas and keeps the last type
    that parses, so a stored ``image/png, text/html`` is an HTML page.
    """
    return {
        part.split(";", 1)[0].strip().lower()
        for part in (content_type or "").split(",")
    }


def is_active_content(content_type):
    """Whether a browser opening *content_type* builds a document that can run
    script. Any ``+xml`` type counts: it renders as an XML document, where an
    element in the XHTML namespace runs as script."""
    return any(
        essence in _ACTIVE_TYPES or essence.endswith("+xml")
        for essence in _essences(content_type)
    )


def _played_by_browser(content_type):
    """Whether the browser shows *content_type* in its own player or viewer,
    which runs none of the file's script. The sandbox would break it: a
    sandboxed video or audio opened in a tab never loads, and Firefox's PDF
    viewer is itself written in JavaScript."""
    return all(
        essence == "application/pdf" or essence.startswith(("video/", "audio/"))
        for essence in _essences(content_type)
    )


def stored_content_headers(content_type):
    """The ``(name, value)`` headers every response carrying stored bytes of
    *content_type* sends, whatever its Content-Disposition."""
    # Leaving a played type unsandboxed trusts the browser to read the bytes
    # as the type they are sent with, never as what they look like.
    headers = [("X-Content-Type-Options", "nosniff")]
    if not _played_by_browser(content_type):
        headers.append(("Content-Security-Policy", STORED_CONTENT_CSP))
    return headers


def protect_stored_content(response, content_type):
    """*response*, which carries stored bytes of *content_type*, with
    ``stored_content_headers``."""
    for name, value in stored_content_headers(content_type):
        response[name] = value
    return response
