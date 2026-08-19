"""A scripted provider for service/API tests: no network, fully observable."""

from contextlib import contextmanager
from io import BytesIO

from workspace.imports.providers.base import (
    KIND_FILES,
    AuthenticationFailed,
    Provider,
    RemoteEntry,
)
from workspace.imports.providers.registry import provider_registry


class FakeFileSource:
    ROOT_ID = "/"

    def __init__(self, tree):
        self._tree = tree

    def list_dir(self, entry_id):
        yield from self._tree.get(entry_id, [])

    @contextmanager
    def open(self, entry):
        yield BytesIO(b"content of " + entry.name.encode())


class FakeProvider(Provider):
    slug = "fake"
    name = "Fake cloud"
    kinds = frozenset({KIND_FILES})

    def __init__(self):
        self.reset()

    def reset(self):
        self.valid_secret = "good"
        self.test_calls = 0
        self.capabilities = {"kinds": ["files"], "quota_used": 42}
        self.tree = {
            "/": [
                RemoteEntry(id="/b.txt", name="b.txt", is_dir=False, size=2),
                RemoteEntry(id="/Zeta", name="Zeta", is_dir=True),
                RemoteEntry(id="/alpha", name="alpha", is_dir=True),
                RemoteEntry(id="/A.txt", name="A.txt", is_dir=False, size=1),
            ],
            "/alpha": [
                RemoteEntry(id="/alpha/deep.txt", name="deep.txt", is_dir=False)
            ],
        }

    def normalize_base_url(self, url, username):
        return url.rstrip("/") + "/dav"

    def test_connection(self, connection):
        self.test_calls += 1
        if connection.get_secret() != self.valid_secret:
            raise AuthenticationFailed("bad secret")
        return dict(self.capabilities)

    def file_source(self, connection):
        return FakeFileSource(self.tree)


def fake_provider():
    provider = provider_registry.get(FakeProvider.slug)
    if provider is None:
        provider = FakeProvider()
        provider_registry.register(provider)
    provider.reset()
    return provider
