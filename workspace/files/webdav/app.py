"""Factory for the WsgiDAV application."""

import logging
from urllib.parse import urlparse

from django.conf import settings as django_settings
from django.db import close_old_connections
from wsgidav.dav_error import (
    HTTP_FORBIDDEN,
    DAVError,
    PRECONDITION_CODE_PropfindFiniteDepth,
)
from wsgidav.default_conf import DEFAULT_CONFIG
from wsgidav.http_authenticator import HTTPAuthenticator
from wsgidav.lock_man.lock_storage import LockStorageDict
from wsgidav.mw.base_mw import BaseMiddleware
from wsgidav.wsgidav_app import WsgiDAVApp

from workspace.common.stored_content import (
    is_active_content,
    stored_content_headers,
)

from .dc import DjangoBasicDomainController
from .provider import WorkspaceDAVProvider

logger = logging.getLogger(__name__)

# Windows Mini-Redirector can hold locks for a very long time on slow
# uploads, then retry — and the old lock blocks the new attempt (423).
# Capping the lock lifetime prevents stale locks from piling up.
# The wsgidav storage classes reference these as ClassName.LOCK_TIME_OUT_*
# so we patch the class attributes directly.
_LOCK_TIMEOUT_DEFAULT = 180  # 3 minutes
_LOCK_TIMEOUT_MAX = 300  # 5 minutes

LockStorageDict.LOCK_TIME_OUT_DEFAULT = _LOCK_TIMEOUT_DEFAULT
LockStorageDict.LOCK_TIME_OUT_MAX = _LOCK_TIMEOUT_MAX


def _build_lock_storage():
    """Return a wsgidav lock storage adapted to the current deployment.

    In production we run several gunicorn workers, so the default
    in-memory ``LockStorageDict`` is unsafe: a LOCK token created on one
    worker is unknown to any other, which breaks Windows Mini-Redirector
    uploads (LOCK + PUT can span separate TCP connections and land on
    different workers — Explorer then reports "file too large").

    When ``settings.WEBDAV_LOCK_STORAGE_URL`` is set we back the lock
    table with Redis so all workers share it. In dev (no Redis) we keep
    the in-process dict, which is fine for a single-worker ``runserver``.
    """
    redis_url = getattr(django_settings, "WEBDAV_LOCK_STORAGE_URL", None)
    if not redis_url:
        logger.info("Using in-memory lock storage (dev mode)")
        return LockStorageDict()

    from wsgidav.lock_man.lock_storage_redis import LockStorageRedis

    LockStorageRedis.LOCK_TIME_OUT_DEFAULT = _LOCK_TIMEOUT_DEFAULT
    LockStorageRedis.LOCK_TIME_OUT_MAX = _LOCK_TIMEOUT_MAX

    parsed = urlparse(redis_url)
    db = int(parsed.path.lstrip("/")) if parsed.path and parsed.path != "/" else 0
    logger.info(
        "Using Redis lock storage (host=%s port=%s db=%s)",
        parsed.hostname or "127.0.0.1",
        parsed.port or 6379,
        db,
    )
    return LockStorageRedis(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        db=db,
        password=parsed.password,
    )


class _RequestConnections:
    """Close the request's database connections, as Django's handler does.

    /dav is served outside Django, so nothing sends ``request_started`` and
    ``request_finished``, the signals that drop a connection once its request
    is over. Under gevent every request has a connection of its own, and one
    left open lingers until a garbage collection reaches it - with its page
    cache on SQLite, out of the pool on PostgreSQL.

    A generator, like the ``WsgiDAVApp`` it wraps: the server closes it when
    the response is sent or the client is gone, which runs the ``finally``.

    Pass-through ``__getattr__`` keeps the wrapped ``WsgiDAVApp`` introspectable
    (``.provider_map``, ``.config`` etc) — used by tests.
    """

    def __init__(self, app):
        self._app = app

    def __call__(self, environ, start_response):
        # Before authentication, which is the first query of a request.
        close_old_connections()
        try:
            yield from self._app(environ, start_response)
        finally:
            close_old_connections()

    def __getattr__(self, name):
        return getattr(self._app, name)


class _StoredContentHeaders:
    """Send a file's bytes with the headers the content endpoints send.

    WebDAV serves them on the app's origin too, outside Django's middleware,
    and a browser that once sent the Basic credentials keeps sending them: an
    HTML file opened under /dav/ would otherwise run as a page of the app.
    """

    def __init__(self, app):
        self._app = app

    def __call__(self, environ, start_response):
        if environ.get("REQUEST_METHOD") not in ("GET", "HEAD"):
            return self._app(environ, start_response)

        def secured_start_response(status, headers, exc_info=None):
            if status.startswith(("200", "206")):
                content_type = next(
                    (v for h, v in headers if h.lower() == "content-type"), ""
                )
                added = stored_content_headers(content_type)
                if is_active_content(content_type):
                    added.append(("Content-Disposition", "attachment"))
                replaced = {name.lower() for name, _ in added}
                headers = [
                    (h, v) for h, v in headers if h.lower() not in replaced
                ] + added
            return start_response(status, headers, exc_info)

        return self._app(environ, secured_start_response)

    def __getattr__(self, name):
        return getattr(self._app, name)


class _FiniteDepthPropfind(BaseMiddleware):
    """Refuse PROPFIND at Depth: infinity, as RFC 4918 9.1 allows.

    wsgidav answers it by collecting every descendant and building the whole
    multistatus in memory, so one request on /dav/ holds the user's entire
    library. A missing Depth header means infinity. Placed after the
    authenticator so an anonymous client still gets its 401 challenge.
    """

    def __call__(self, environ, start_response):
        if (
            environ.get("REQUEST_METHOD") == "PROPFIND"
            and environ.get("HTTP_DEPTH", "infinity").strip().lower() == "infinity"
        ):
            raise DAVError(
                HTTP_FORBIDDEN, err_condition=PRECONDITION_CODE_PropfindFiniteDepth
            )
        return self.next_app(environ, start_response)


def _middleware_stack():
    stack = list(DEFAULT_CONFIG["middleware_stack"])
    stack.insert(stack.index(HTTPAuthenticator) + 1, _FiniteDepthPropfind)
    return stack


def create_webdav_app():
    """Build and return a configured ``WsgiDAVApp``."""
    config = {
        "mount_path": "/dav",
        "provider_mapping": {"/": WorkspaceDAVProvider()},
        "middleware_stack": _middleware_stack(),
        "http_authenticator": {
            "domain_controller": DjangoBasicDomainController,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        "verbose": 1,
        "logging": {
            "enable": True,
            "enable_loggers": [],
        },
        "property_manager": False,
        # No lock storage in the config: WsgiDAVApp would wrap it in a plain
        # LockManager, and the storage classes refuse to be opened twice, so
        # the manager we actually want has to be the first one built over it.
        "lock_storage": None,
        "dir_browser": {
            "enable": False,
        },
    }
    app = WsgiDAVApp(config)
    _install_app_lock_manager(app, _build_lock_storage())
    return _RequestConnections(_StoredContentHeaders(app))


def _install_app_lock_manager(app, storage):
    """Give *app* the lock manager that mirrors DAV locks onto ``File.locked_by``.

    Both the app and every provider hold a reference; the provider's is the
    one each resource reads, and its presence is also what makes OPTIONS
    advertise ``DAV: 1,2``.
    """
    from .locks import AppLockManager

    manager = AppLockManager(storage)
    app.lock_manager = manager
    for provider in app.provider_map.values():
        provider.set_lock_manager(manager)
