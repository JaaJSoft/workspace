"""Backend selection for image embeddings (PHOTOS_EMBEDDING_BACKEND), and
whether the instance embeds its photos at all (PHOTOS_EMBEDDINGS_ENABLED)."""

from __future__ import annotations

import logging

from django.conf import settings

from workspace.common.logging import scrub

from ..detection.base import BackendHealth
from .base import EmbeddingBackend


def _clip_vit_b32():
    from .local import ClipVitB32Backend

    return ClipVitB32Backend()


def _siglip2_base():
    from .local import Siglip2BaseBackend

    return Siglip2BaseBackend()


def _jina_clip_v1():
    from .local import JinaClipV1Backend

    return JinaClipV1Backend()


def _remote():
    from .remote import RemoteEmbeddingBackend

    return RemoteEmbeddingBackend()


def _fake():
    from .fake import FakeEmbeddingBackend

    return FakeEmbeddingBackend()


_BACKENDS = {
    "clip_vit_b32": _clip_vit_b32,
    "siglip2_base": _siglip2_base,
    "jina_clip_v1": _jina_clip_v1,
    "remote": _remote,
    "fake": _fake,
}

logger = logging.getLogger(__name__)


class BackendMisconfigured(RuntimeError):
    pass


class MisconfiguredBackend(EmbeddingBackend):
    """Stands in for a PHOTOS_EMBEDDING_BACKEND value that names no backend.

    As for faces: every embedding fails, the photos stay pending, and the
    admin dashboard says why, rather than the feature silently switching off.
    """

    def __init__(self, key):
        self.key = key

    def _message(self):
        return f"unknown embedding backend {self.key!r}"

    def embed_image(self, image):
        raise BackendMisconfigured(self._message())

    def embed_text(self, text):
        raise BackendMisconfigured(self._message())

    def health(self):
        return BackendHealth(ok=False, detail=self._message())


def embeddings_available():
    """Whether this instance embeds its photos."""
    return bool(settings.PHOTOS_EMBEDDINGS_ENABLED)


def backend_keys():
    """The keys PHOTOS_EMBEDDING_BACKEND accepts, the tests-only fake aside."""
    return sorted(key for key in _BACKENDS if key != "fake")


def get_embedding_backend(key=None):
    """The EmbeddingBackend *key* names, the configured one by default.

    Never None, even when misconfigured.
    """
    key = settings.PHOTOS_EMBEDDING_BACKEND if key is None else key
    factory = _BACKENDS.get(key)
    if factory is None:
        logger.error("Unknown embedding backend %s", scrub(key))
        return MisconfiguredBackend(key)
    return factory()


def is_misconfigured():
    return settings.PHOTOS_EMBEDDING_BACKEND not in _BACKENDS


def configured_dims(default=768):
    """The embedding size of the configured backend, *default* when unknown."""
    if is_misconfigured():
        return default
    return get_embedding_backend().dims


def _threshold(setting, default):
    value = getattr(settings, setting)
    return default if value is None else value


def similar_max_distance():
    return _threshold(
        "PHOTOS_SIMILAR_MAX_DISTANCE", get_embedding_backend().similar_max_distance
    )


def duplicate_max_distance():
    return _threshold(
        "PHOTOS_DUPLICATE_MAX_DISTANCE",
        get_embedding_backend().duplicate_max_distance,
    )


def description_max_distance():
    return _threshold(
        "PHOTOS_DESCRIPTION_MAX_DISTANCE",
        get_embedding_backend().description_max_distance,
    )


def tag_thresholds():
    """The backend's automatic tags: {tag text: max distance}, the classic
    tags it recognizes."""
    return dict(get_embedding_backend().tag_thresholds)
