"""The scanner interface every malware backend implements.

``scan`` takes a file-like object rather than a path on purpose: a blob may
live on a storage backend with no filesystem, so a path-based API would tie
the feature to FileSystemStorage.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class ScanVerdict:
    """The outcome of scanning one blob. ``status`` is a FileScan.Status value."""

    status: str
    signature: str = ""
    detail: str = ""


@dataclass(frozen=True)
class ScannerHealth:
    """Whether the backend is reachable, for the admin dashboard."""

    reachable: bool
    version: str = ""
    error: str = ""


class Scanner(abc.ABC):
    """A malware scanner backend."""

    @abc.abstractmethod
    def scan(self, stream, *, name=""):
        """Return a ScanVerdict for the bytes readable from *stream*.

        Never raises for an operational failure: an unreachable daemon or a
        malformed reply is an ERROR verdict, because the caller's policy - not
        the backend - decides what an unscannable file may do.
        """

    @abc.abstractmethod
    def health(self):
        """Return a ScannerHealth describing whether the backend answers."""
