import threading

from .base import Provider


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, Provider] = {}
        self._lock = threading.Lock()

    def register(self, provider: Provider):
        with self._lock:
            if provider.slug in self._providers:
                raise ValueError(f"Provider '{provider.slug}' is already registered")
            self._providers[provider.slug] = provider

    def get(self, slug: str) -> Provider | None:
        return self._providers.get(slug)

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def available(self) -> list[Provider]:
        return [p for p in self._providers.values() if p.is_available()]


provider_registry = ProviderRegistry()


def register_builtin_providers():
    from .nextcloud import NextcloudProvider
    from .webdav import WebDavProvider

    for provider in (WebDavProvider(), NextcloudProvider()):
        if provider_registry.get(provider.slug) is None:
            provider_registry.register(provider)
