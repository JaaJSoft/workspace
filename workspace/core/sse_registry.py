import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

import orjson
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSEProviderInfo:
    slug: str
    provider_cls: type  # subclass of SSEProvider


class SSEProvider(ABC):
    """Instantiated once per SSE connection per provider."""

    def __init__(self, user, last_event_id: str | None):
        self.user = user
        self.last_event_id = last_event_id

    @abstractmethod
    def get_initial_events(self) -> list[tuple[str, dict, str | None]]:
        """Events sent immediately on connection.

        Returns list of (event_name, data_dict, event_id_or_None).
        """

    @abstractmethod
    def poll(self, cache_value: str | None) -> list[tuple[str, dict, str | None]]:
        """Called every ~2s.

        cache_value is non-None when the dirty flag changed, None otherwise.
        Returns list of (event_name, data_dict, event_id_or_None).
        """


class SSERegistry:
    """Singleton thread-safe registry for SSE providers."""

    def __init__(self):
        self._providers: dict[str, SSEProviderInfo] = {}
        self._lock = threading.Lock()

    def register(self, provider_info: SSEProviderInfo):
        with self._lock:
            if provider_info.slug in self._providers:
                raise ValueError(
                    f"SSE provider with slug '{provider_info.slug}' is already registered"
                )
            self._providers[provider_info.slug] = provider_info

    def get_all(self) -> dict[str, SSEProviderInfo]:
        return dict(self._providers)


sse_registry = SSERegistry()


def _get_redis():
    """Return a raw Redis connection, or None if Redis is not the cache backend."""
    try:
        from django_redis import get_redis_connection

        return get_redis_connection("default")
    except Exception:
        return None


def notify_sse(provider_slug: str, user_id: int):
    """Notify an SSE provider that new data is available for a user.

    Uses Redis Pub/Sub for near-instant delivery when Redis is available,
    falls back to cache dirty flags for local dev without Redis.
    """
    redis = _get_redis()
    if redis is not None:
        try:
            redis.publish(
                f'sse:user:{user_id}',
                orjson.dumps({'provider': provider_slug}),
            )
            return
        except Exception:
            logger.warning(
                "Redis publish failed for SSE notify (provider=%s, user=%s), "
                "falling back to cache",
                provider_slug,
                user_id,
                exc_info=True,
            )

    # Fallback: cache dirty flag (local dev or Redis failure)
    cache.set(
        f'sse:{provider_slug}:last_event:{user_id}',
        timezone.now().isoformat(),
        120,
    )
