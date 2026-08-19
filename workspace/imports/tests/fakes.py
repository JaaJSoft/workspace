"""A scripted provider for service/API tests: no network, fully observable."""

from contextlib import contextmanager
from io import BytesIO

from workspace.imports.providers.base import (
    KIND_FILES,
    AuthenticationFailed,
    ConnectionFailed,
    Provider,
    RemoteEntry,
    RemoteNotFound,
)
from workspace.imports.providers.registry import provider_registry


class FakeFileSource:
    ROOT_ID = "/"

    def __init__(self, tree, *, contents=None, fail_list=(), fail_open=()):
        self._tree = tree
        self._contents = contents or {}
        self.fail_list = set(fail_list)
        self.fail_open = set(fail_open)
        self.closed = False
        self.opened = []

    def close(self):
        self.closed = True

    def list_dir(self, entry_id):
        if entry_id in self.fail_list:
            raise ConnectionFailed(f"cannot list {entry_id}")
        yield from self._tree.get(entry_id, [])

    @contextmanager
    def open(self, entry):
        self.opened.append(entry.id)
        if entry.id in self.fail_open:
            raise RemoteNotFound(f"{entry.id} vanished")
        yield BytesIO(
            self._contents.get(entry.id, b"content of " + entry.name.encode())
        )


class FakeProvider(Provider):
    slug = "fake"
    name = "Fake cloud"
    kinds = frozenset({KIND_FILES})

    def __init__(self):
        self.reset()

    def reset(self):
        self.valid_secret = "good"
        self.test_calls = 0
        self.last_source = None
        self.contents = {}
        self.fail_list = set()
        self.fail_open = set()
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
        self.last_source = FakeFileSource(
            self.tree,
            contents=self.contents,
            fail_list=self.fail_list,
            fail_open=self.fail_open,
        )
        return self.last_source


def fake_provider():
    provider = provider_registry.get(FakeProvider.slug)
    if provider is None:
        provider = FakeProvider()
        provider_registry.register(provider)
    provider.reset()
    return provider
