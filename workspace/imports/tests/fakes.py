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
    RemoteTag,
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


class FakeMetadataSource:
    def __init__(self, favorites, tags, *, fail=None):
        self._favorites = favorites
        self._tags = tags
        self.fail = fail
        self.closed = False
        self.tagged_calls = []

    def close(self):
        self.closed = True

    def favorites(self):
        if self.fail == "favorites":
            raise ConnectionFailed("cannot read favorites")
        yield from self._favorites

    def tags(self):
        """``_tags`` is a list of ``(id, name, [entry ids])``."""
        if self.fail == "tags":
            raise ConnectionFailed("cannot read tags")
        for tag_id, name, _entries in self._tags:
            yield RemoteTag(id=tag_id, name=name)

    def tagged(self, tag_id):
        self.tagged_calls.append(tag_id)
        if self.fail == f"tagged:{tag_id}":
            raise ConnectionFailed(f"cannot read tag {tag_id}")
        for known_id, _name, entries in self._tags:
            if known_id == tag_id:
                yield from entries


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
        self.last_metadata = None
        # None until a test opts in, so the metadata phase stays a no-op for
        # every test that only cares about the copy.
        self.favorites = None
        self.tags = []
        self.fail_metadata = None
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

    def file_metadata_source(self, connection):
        if self.favorites is None and not self.tags:
            return None
        self.last_metadata = FakeMetadataSource(
            self.favorites or [], self.tags, fail=self.fail_metadata
        )
        return self.last_metadata


def fake_provider():
    provider = provider_registry.get(FakeProvider.slug)
    if provider is None:
        provider = FakeProvider()
        provider_registry.register(provider)
    provider.reset()
    return provider
