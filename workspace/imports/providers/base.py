"""Provider and data-source contracts.

A *provider* is a kind of remote source (WebDAV host, Nextcloud, later an
OAuth drive). It knows how to authenticate an ``ImportConnection`` and which
data *kinds* it can supply. Per kind it hands out a source object - for files,
a ``FileSource`` - which the importers read from. Providers are stateless
singletons; all per-user state lives on the connection.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, ClassVar, Protocol

from ..errors import ImportsError

KIND_FILES = "files"
KIND_CONTACTS = "contacts"


class ProviderError(ImportsError):
    """The remote could not be used."""


class ConnectionFailed(ProviderError):
    """The remote could not be reached (DNS, TCP, TLS, timeout)."""


class AuthenticationFailed(ProviderError):
    """The remote rejected the credentials."""


class RemoteNotFound(ProviderError):
    """The requested remote path does not exist."""


@dataclass(frozen=True)
class RemoteEntry:
    """One entry of a remote file tree.

    ``id`` is the provider-specific stable identifier (a path for WebDAV, an
    opaque id for drives); it is what ``FileSource.list_dir`` takes and what
    job items record.
    """

    id: str
    name: str
    is_dir: bool
    size: int | None = None
    modified_at: datetime | None = None
    etag: str = ""
    mime_type: str = ""

    @property
    def fingerprint(self) -> str:
        """What identifies this version of the entry: the etag, else size and
        mtime. Empty when the provider gives nothing - then the entry is never
        considered already imported."""
        if self.etag:
            return self.etag
        if self.size is not None and self.modified_at is not None:
            return f"{self.size}:{self.modified_at.timestamp():.0f}"
        return ""

    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "is_dir": self.is_dir,
            "size": self.size,
            "modified_at": self.modified_at.isoformat() if self.modified_at else None,
            "etag": self.etag,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True)
class RemoteTag:
    """A label defined on the remote and assignable to its entries. ``id`` is
    what ``FileMetadataSource.tagged`` takes; ``name`` is all that is carried
    over - colours and visibility rules stay on the remote."""

    id: str
    name: str


@dataclass(frozen=True)
class RemoteAddressBook:
    """An address book on the remote. ``id`` is its path relative to the
    provider's CardDAV root; it is what ``ContactSource.card_refs`` takes."""

    id: str
    name: str
    description: str = ""

    def as_dict(self):
        return {"id": self.id, "name": self.name, "description": self.description}


@dataclass(frozen=True)
class RemoteCard:
    """One contact resource: its path, its version marker, its vCard text."""

    id: str
    etag: str
    text: str


class FileSource(Protocol):
    """Read access to a remote file tree."""

    ROOT_ID: ClassVar[str]

    def list_dir(self, entry_id: str) -> Iterator[RemoteEntry]:
        """Yield the direct children of a directory - one level, never the tree."""

    def open(self, entry: RemoteEntry) -> AbstractContextManager[BinaryIO]:
        """Stream a file's bytes; the body is never buffered in full."""

    def close(self) -> None:
        """Release the underlying connection pool; the source is unusable after."""


class FileMetadataSource(Protocol):
    """What a remote knows about its files besides their bytes.

    Read once the copy is over, and always by entry id, so the importer can
    match each answer against the entries it has recorded.
    """

    def favorites(self) -> Iterator[str]:
        """Entry ids the connection's user marked as favorite, files and
        folders alike."""

    def tags(self) -> Iterator[RemoteTag]:
        """Tags the connection's user can see."""

    def tagged(self, tag_id: str) -> Iterator[str]:
        """Entry ids carrying the tag."""

    def close(self) -> None:
        """Release the underlying connection pool."""


class ContactSource(Protocol):
    """Read access to a remote's address books."""

    def address_books(self) -> Iterator[RemoteAddressBook]:
        """The address books the connection's user can read."""

    def card_refs(self, book_id: str) -> Iterator[tuple[str, str]]:
        """``(card id, etag)`` for every contact of a book - no card bodies."""

    def fetch_cards(self, book_id: str, card_ids: list[str]) -> Iterator[RemoteCard]:
        """The cards asked for; one the remote no longer has is left out."""

    def fetch_photo(self, url: str) -> tuple[bytes, str] | None:
        """``(bytes, image subtype)`` of a photo a card links to, or ``None``
        when it cannot or must not be fetched."""

    def close(self) -> None:
        """Release the underlying connection pool."""


class Provider(ABC):
    slug: ClassVar[str]
    name: ClassVar[str]
    #: "credentials" (URL + username + secret) or "oauth2" (#696).
    auth: ClassVar[str] = "credentials"
    kinds: ClassVar[frozenset[str]] = frozenset()

    def is_available(self) -> bool:
        """Whether this instance can offer the provider (OAuth client configured...)."""
        return True

    def normalize_base_url(self, url: str, username: str) -> str:
        """Turn what the user typed into the URL the provider actually talks to."""
        return url.rstrip("/")

    @abstractmethod
    def test_connection(self, connection) -> dict:
        """Check the credentials and return the capabilities to cache on the
        connection. Raises ``ProviderError`` when the remote is unusable."""

    def file_source(self, connection) -> FileSource:
        raise NotImplementedError(f"{self.slug} does not provide files")

    def file_metadata_source(self, connection) -> FileMetadataSource | None:
        """Favorites and tags for the files this provider served, or ``None``
        when it has none to offer - then the importer skips that phase."""
        return None

    def contact_source(self, connection) -> ContactSource:
        raise NotImplementedError(f"{self.slug} does not provide contacts")

    def describe(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "auth": self.auth,
            "kinds": sorted(self.kinds),
        }
