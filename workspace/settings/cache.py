"""Cache and session backends.

Also owns the Redis URL derivation used by the Celery and WebDAV settings.
The ``_REDIS_*`` names are deliberately private: they are wiring for other
settings modules, not settings themselves, and must not leak into
``django.conf.settings``.
"""

import os
from urllib.parse import urlparse, urlunparse

# Default: in-memory cache for local/dev. Optional Redis via env.
# When Redis is available, separate DBs are used to isolate concerns:
#   DB 0 — cache (evictable)
#   DB 1 — sessions (must not be evicted)
#   DB 2 — Celery broker + results
#   DB 3 — WebDAV lock storage (shared across gunicorn workers)
_REDIS_URL = os.getenv("REDIS_URL") or os.getenv("DJANGO_REDIS_URL")


def _redis_db_url(base_url, db_number):
    """Derive a Redis URL pointing to a specific DB number."""
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path=f"/{db_number}"))


if _REDIS_URL:
    _REDIS_CACHE_URL = _redis_db_url(_REDIS_URL, 0)
    _REDIS_SESSION_URL = _redis_db_url(_REDIS_URL, 1)
    _REDIS_CELERY_URL = _redis_db_url(_REDIS_URL, 2)
    _REDIS_WEBDAV_URL = _redis_db_url(_REDIS_URL, 3)

    # Cache/session value compression. Defaults to zlib (stdlib, always
    # available); set REDIS_CACHE_COMPRESSION to gzip/lzma/lz4/zstd to override
    # (lz4 and zstd need the lz4 / pyzstd package installed on the host). Any
    # other value, e.g. "off" or "none", disables compression.
    _REDIS_COMPRESSORS = {
        "zlib": "django_redis.compressors.zlib.ZlibCompressor",
        "gzip": "django_redis.compressors.gzip.GzipCompressor",
        "lzma": "django_redis.compressors.lzma.LzmaCompressor",
        "lz4": "django_redis.compressors.lz4.Lz4Compressor",
        "zstd": "django_redis.compressors.zstd.ZStdCompressor",
    }
    _REDIS_COMPRESSOR = _REDIS_COMPRESSORS.get(
        os.getenv("REDIS_CACHE_COMPRESSION", "zlib").strip().lower()
    )
    _REDIS_COMPRESSOR_OPTION = (
        {"COMPRESSOR": _REDIS_COMPRESSOR} if _REDIS_COMPRESSOR else {}
    )

    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _REDIS_CACHE_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                **_REDIS_COMPRESSOR_OPTION,
            },
            "TIMEOUT": None,  # Infinite by default; specific features manage their own TTL
        },
        "sessions": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _REDIS_SESSION_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                **_REDIS_COMPRESSOR_OPTION,
            },
            "TIMEOUT": None,
        },
    }

    # Use dedicated Redis DB for sessions (isolated from cache evictions)
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    SESSION_CACHE_ALIAS = "sessions"
else:
    _REDIS_CELERY_URL = None
    _REDIS_WEBDAV_URL = None

    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "Workspace-service-locmem",
            "TIMEOUT": None,
        }
    }
    # Fall back to DB sessions when Redis is not available
    SESSION_ENGINE = "django.contrib.sessions.backends.db"
