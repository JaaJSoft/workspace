"""Backend selection for sensitive-content detection (PHOTOS_SENSITIVE_BACKEND),
and whether the instance runs it at all (PHOTOS_SENSITIVE_DETECTION_ENABLED)."""

from __future__ import annotations

import logging

from django.conf import settings

from workspace.common.logging import scrub

from ..detection.base import BackendHealth
from .base import SensitiveBackend


def _falconsai_vit():
    from .vit import FalconsaiVitBackend

    return FalconsaiVitBackend()


def _fake():
    from .fake import FakeSensitiveBackend

    return FakeSensitiveBackend()


_BACKENDS = {
    "falconsai_vit": _falconsai_vit,
    "fake": _fake,
}

logger = logging.getLogger(__name__)


class BackendMisconfigured(RuntimeError):
    pass


class MisconfiguredBackend(SensitiveBackend):
    """Stands in for a PHOTOS_SENSITIVE_BACKEND value that names no backend:
    every check fails and the admin dashboard says why."""

    def __init__(self, key):
        self.key = key

    def _message(self):
        return f"unknown sensitive-content backend {self.key!r}"

    def score(self, image):
        raise BackendMisconfigured(self._message())

    def health(self):
        return BackendHealth(ok=False, detail=self._message())


def detection_available():
    """Whether this instance checks its photos for sensitive content."""
    return bool(settings.PHOTOS_SENSITIVE_DETECTION_ENABLED)


def backend_keys():
    """The keys PHOTOS_SENSITIVE_BACKEND accepts, the tests-only fake aside."""
    return sorted(key for key in _BACKENDS if key != "fake")


def get_sensitive_backend(key=None):
    """The SensitiveBackend *key* names, the configured one by default.

    Never None, even when misconfigured.
    """
    key = settings.PHOTOS_SENSITIVE_BACKEND if key is None else key
    factory = _BACKENDS.get(key)
    if factory is None:
        logger.error("Unknown sensitive-content backend %s", scrub(key))
        return MisconfiguredBackend(key)
    return factory()


def is_misconfigured():
    return settings.PHOTOS_SENSITIVE_BACKEND not in _BACKENDS
