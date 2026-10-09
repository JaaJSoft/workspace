"""
WSGI config for workspace project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import io
import os
import threading

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")

_django_app = get_wsgi_application()

_webdav_app = None
_webdav_lock = threading.Lock()

DAV_PREFIX = "/dav"


def _get_webdav_app():
    global _webdav_app
    if _webdav_app is None:
        with _webdav_lock:
            if _webdav_app is None:
                from workspace.files.webdav.app import create_webdav_app

                _webdav_app = create_webdav_app()
    return _webdav_app


_WEBDAV_METHODS = {"PROPFIND", "PROPPATCH", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"}

CALDAV_ROOT = "/caldav/"

# A discovery PROPFIND is a few hundred bytes; anything past this is not one.
_DISCOVERY_BODY_LIMIT = 64 * 1024


def _is_caldav_discovery(environ):
    """True for a PROPFIND on "/" from a calendar client looking for its account.

    Clients given only the server address ask "/" who the user is
    (``current-user-principal``) before trying ``/.well-known/caldav``, and
    some take whatever answers as the principal. The file WebDAV app owns
    "/", and knows nothing about calendars. The body is put back for
    whichever app ends up reading it.
    """
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return False
    if not 0 < length <= _DISCOVERY_BODY_LIMIT:
        return False
    body = environ["wsgi.input"].read(length)
    environ["wsgi.input"] = io.BytesIO(body)
    return b"current-user-principal" in body or b"calendar-home-set" in body


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "")

    if path == DAV_PREFIX or path.startswith(DAV_PREFIX + "/"):
        # Strip the /dav prefix so WsgiDAV sees paths relative to its root.
        environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + DAV_PREFIX
        environ["PATH_INFO"] = path[len(DAV_PREFIX) :] or "/"
        return _get_webdav_app()(environ, start_response)

    if path == "/" and method == "PROPFIND" and _is_caldav_discovery(environ):
        environ["PATH_INFO"] = CALDAV_ROOT
        return _django_app(environ, start_response)

    # Windows WebDAV MiniRedir sends PROPFIND to "/" to check quota before
    # uploading to /dav.  Route WebDAV methods on the root to the DAV app
    # so the client gets a proper multistatus response instead of a 403.
    if path == "/" and method in _WEBDAV_METHODS:
        environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + DAV_PREFIX
        environ["PATH_INFO"] = "/"
        return _get_webdav_app()(environ, start_response)

    return _django_app(environ, start_response)
