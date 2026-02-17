import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone


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


def notify_sse(provider_slug: str, user_id: int):
    """Set the dirty flag cache for a provider/user pair."""
    cache.set(
        f'sse:{provider_slug}:last_event:{user_id}',
        timezone.now().isoformat(),
        120,
    )
