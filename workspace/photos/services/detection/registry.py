"""Backend selection for face detection (PHOTOS_FACE_BACKEND)."""

from __future__ import annotations

import logging

from django.conf import settings

from workspace.common.logging import scrub

from .base import BackendHealth, FaceBackend


def _yunet_sface():
    from .yunet_sface import YuNetSFaceBackend

    return YuNetSFaceBackend()


def _scrfd_arcface():
    from .scrfd_arcface import ScrfdArcFaceBackend

    return ScrfdArcFaceBackend()


def _fake():
    from .fake import FakeFaceBackend

    return FakeFaceBackend()


_BACKENDS = {
    "yunet_sface": _yunet_sface,
    "scrfd_arcface": _scrfd_arcface,
    "fake": _fake,
}

logger = logging.getLogger(__name__)


class BackendMisconfigured(RuntimeError):
    pass


class MisconfiguredBackend(FaceBackend):
    """Stands in for a PHOTOS_FACE_BACKEND value that names no backend.

    Returning None instead would read as "faces are off", so a typo in the
    setting would silently disable a feature the administrator believes is
    on. This one fails every analysis - the photos stay pending and come back
    once the setting is fixed - and says why on the admin dashboard.
    """

    def __init__(self, key):
        self.key = key

    def _message(self):
        return f"unknown face backend {self.key!r}"

    def detect(self, image):
        raise BackendMisconfigured(self._message())

    def embed(self, aligned):
        raise BackendMisconfigured(self._message())

    def health(self):
        return BackendHealth(ok=False, detail=self._message())


def backend_keys():
    """The keys PHOTOS_FACE_BACKEND accepts, the tests-only fake aside."""
    return sorted(key for key in _BACKENDS if key != "fake")


def get_face_backend(key=None):
    """The FaceBackend *key* names, the configured one by default.

    Never None, even when misconfigured.
    """
    key = settings.PHOTOS_FACE_BACKEND if key is None else key
    factory = _BACKENDS.get(key)
    if factory is None:
        logger.error("Unknown face backend %s", scrub(key))
        return MisconfiguredBackend(key)
    return factory()


def is_misconfigured():
    return settings.PHOTOS_FACE_BACKEND not in _BACKENDS


def configured_dims(default=128):
    """The embedding size of the configured backend, *default* when unknown."""
    if is_misconfigured():
        return default
    return get_face_backend().dims
