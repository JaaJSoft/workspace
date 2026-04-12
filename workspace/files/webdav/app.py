"""Factory for the WsgiDAV application."""

import logging
from urllib.parse import urlparse

from django.conf import settings as django_settings
from wsgidav.lock_man.lock_storage import LockStorageDict
from wsgidav.wsgidav_app import WsgiDAVApp

from .dc import DjangoBasicDomainController
from .provider import WorkspaceDAVProvider

logger = logging.getLogger(__name__)

# Windows Mini-Redirector can hold locks for a very long time on slow
# uploads, then retry — and the old lock blocks the new attempt (423).
# Capping the lock lifetime prevents stale locks from piling up.
_LOCK_TIMEOUT_DEFAULT = 180  # 3 minutes
_LOCK_TIMEOUT_MAX = 300  # 5 minutes


class _ShortLockStorageDict(LockStorageDict):
    LOCK_TIME_OUT_DEFAULT = _LOCK_TIMEOUT_DEFAULT
    LOCK_TIME_OUT_MAX = _LOCK_TIMEOUT_MAX


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
        return _ShortLockStorageDict()

    from wsgidav.lock_man.lock_storage_redis import LockStorageRedis

    class _ShortLockStorageRedis(LockStorageRedis):
        LOCK_TIME_OUT_DEFAULT = _LOCK_TIMEOUT_DEFAULT
        LOCK_TIME_OUT_MAX = _LOCK_TIMEOUT_MAX

    parsed = urlparse(redis_url)
    db = int(parsed.path.lstrip("/")) if parsed.path and parsed.path != "/" else 0
    logger.info(
        "Using Redis lock storage (host=%s port=%s db=%s)",
        parsed.hostname or "127.0.0.1", parsed.port or 6379, db,
    )
    return _ShortLockStorageRedis(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        db=db,
        password=parsed.password,
    )


def create_webdav_app():
    """Build and return a configured ``WsgiDAVApp``."""
    config = {
        "mount_path": "/dav",
        "provider_mapping": {"/": WorkspaceDAVProvider()},
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
        "lock_storage": _build_lock_storage(),
        "dir_browser": {
            "enable": False,
        },
    }
    return WsgiDAVApp(config)
