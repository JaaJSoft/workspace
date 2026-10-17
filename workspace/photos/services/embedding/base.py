"""The interface every image embedding backend implements.

A backend turns a picture, and a line of text, into vectors of one shared
space: a photo's vector lands near the vectors of the photos that look alike,
and near the vector of a sentence describing it. That one vector per photo is
what similar photos, near-duplicates and search by description all read.

It is not a way to recognize people: a whole-picture vector says what the
scene is, not who is in it. Identity is the face pipeline's (detection/).
"""

from __future__ import annotations

import abc

from ..detection.weights import ensure_all


class EmbeddingBackend(abc.ABC):
    """An image and text embedding backend."""

    # The registry key (PHOTOS_EMBEDDING_BACKEND).
    key: str = ""
    # Embedding size. The vector index is declared with the configured one.
    dims: int = 0
    # The weights files it runs (detection.weights.ModelFile).
    models: tuple = ()
    # Longest side, in px, of the copy of the photo handed to embed_image.
    decode_size: int = 512
    # Cosine distances calibrated to the embedding space, each overridable by
    # its PHOTOS_*_MAX_DISTANCE setting: two photos under `similar` look
    # alike, under `duplicate` they are near-copies (a burst, a re-export),
    # and a photo past `description` from a query text does not show it.
    similar_max_distance: float = 0.25
    duplicate_max_distance: float = 0.06
    description_max_distance: float = 0.8
    # Automatic tags: {classic tag text: max distance}, fitted per tag by
    # the bench (scripts/embedding_bench.py prints the table). A tag left out
    # is one the model does not tell apart well enough; none by default, a
    # remote model being uncalibrated.
    tag_thresholds: dict = {}
    # The same on a video, from the closest of its key frames: a compressed,
    # often blurred frame, and several draws a video.
    video_tag_thresholds: dict = {}

    @property
    def model_key(self):
        """What is recorded with every embedding: a photo embedded under
        another key counts as pending again, its vector being of another
        space."""
        return self.key

    def prepare(self):
        """Download and verify the weights; a no-op once done in this process.

        Raises detection.weights.WeightsError when they cannot be had.
        """
        ensure_all(self.models)

    @abc.abstractmethod
    def embed_image(self, image):
        """The embedding of *image*, a PIL RGB image as displayed (EXIF
        orientation applied), at most ``decode_size`` on its longest side.

        Not normalized: the vector index scales it to unit length. Raises for
        an operational failure (weights missing, endpoint down): the pipeline
        records nothing, and the photo stays pending.
        """

    @abc.abstractmethod
    def embed_text(self, text):
        """The embedding of *text*, in the space of embed_image's."""

    @abc.abstractmethod
    def health(self):
        """A BackendHealth: whether the backend can run, for the dashboard."""
