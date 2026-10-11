"""A remote backend: an OpenAI-compatible ``/embeddings`` endpoint serving a
multimodal model (LiteLLM in front of one, Infinity, a Jina, Cohere or Voyage
deployment...).

There is no standard for image input on ``/embeddings``. The contract used
here is the most common one: a photo goes in ``input`` as a ``data:`` URI of a
downscaled JPEG (never the original), a query text as a plain string, and the
model embeds both into one space. The endpoint must answer with vectors of
PHOTOS_EMBEDDING_DIMS components.

Every photo of the instance is sent to that endpoint: enabling it is an
instance-level decision, stated in docs/photos/README.md.
"""

from __future__ import annotations

import base64
import io

from django.conf import settings

from ..detection.base import BackendHealth
from .base import EmbeddingBackend

_JPEG_QUALITY = 85


class RemoteEmbeddingError(RuntimeError):
    pass


def image_data_uri(image):
    """*image* as a ``data:image/jpeg;base64,`` URI."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class RemoteEmbeddingBackend(EmbeddingBackend):
    key = "remote"

    @property
    def dims(self):
        return settings.PHOTOS_EMBEDDING_DIMS

    @property
    def decode_size(self):
        return settings.PHOTOS_EMBEDDING_REMOTE_IMAGE_SIZE

    @property
    def model_key(self):
        # The model is part of the space: another model behind the same
        # endpoint makes every photo pending again.
        return f"remote:{settings.PHOTOS_EMBEDDING_MODEL}"[:128]

    def _missing(self):
        return [
            name
            for name in ("PHOTOS_EMBEDDING_BASE_URL", "PHOTOS_EMBEDDING_MODEL")
            if not getattr(settings, name)
        ]

    def _client(self):
        missing = self._missing()
        if missing:
            raise RemoteEmbeddingError(f"{', '.join(missing)} not set")
        from openai import OpenAI

        return OpenAI(
            api_key=settings.PHOTOS_EMBEDDING_API_KEY
            or settings.AI_API_KEY
            or "unused",
            base_url=settings.PHOTOS_EMBEDDING_BASE_URL,
            timeout=settings.PHOTOS_EMBEDDING_TIMEOUT,
            max_retries=1,
        )

    def _embed(self, value):
        response = self._client().embeddings.create(
            model=settings.PHOTOS_EMBEDDING_MODEL,
            input=[value],
            # Plain floats: not every server implements the SDK's default,
            # base64.
            encoding_format="float",
        )
        if not response.data:
            raise RemoteEmbeddingError("the endpoint returned no embedding")
        vector = response.data[0].embedding
        if len(vector) != self.dims:
            raise RemoteEmbeddingError(
                f"the endpoint returned {len(vector)} components, "
                f"PHOTOS_EMBEDDING_DIMS is {self.dims}"
            )
        return vector

    def embed_image(self, image):
        return self._embed(image_data_uri(image))

    def embed_text(self, text):
        return self._embed(text)

    def health(self):
        missing = self._missing()
        if missing:
            return BackendHealth(ok=False, detail=f"{', '.join(missing)} not set")
        return BackendHealth(
            ok=True,
            detail=f"{settings.PHOTOS_EMBEDDING_MODEL} at "
            f"{settings.PHOTOS_EMBEDDING_BASE_URL}, {self.dims}-d",
        )
