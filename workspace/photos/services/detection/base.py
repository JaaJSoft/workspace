"""The interface every face detection backend implements.

A backend does two things: find faces in a picture, and turn an aligned face
into an embedding - a vector whose cosine distance to another face's says how
alike the two people look. Everything around it (decoding the original,
alignment, quality, crops, storage, grouping) is the pipeline's, so a backend
stays a thin wrapper over its models.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np

from .weights import ensure_all

# The five landmarks every backend returns, in this order, in the pixel
# coordinates of the image it was given.
LANDMARKS = ("right_eye", "left_eye", "nose", "right_mouth", "left_mouth")


@dataclass(frozen=True)
class DetectedFace:
    """One face found in an image, in that image's pixel coordinates."""

    # x, y, width, height of the box.
    box: tuple[float, float, float, float]
    # 5 x 2, in LANDMARKS order.
    landmarks: np.ndarray
    # The detector's confidence, 0 to 1.
    score: float


@dataclass(frozen=True)
class BackendHealth:
    """Whether the backend can run, for the admin dashboard."""

    ok: bool
    detail: str = ""


class FaceBackend(abc.ABC):
    """A face detection and embedding backend."""

    # The registry key (PHOTOS_FACE_BACKEND). Recorded with every analysis, so
    # a photo analyzed by another backend counts as pending again.
    key: str = ""
    # Embedding size. The vector index is declared with the default backend's.
    dims: int = 0
    # Cosine distance under which two embeddings are the same person: the
    # threshold is a property of the embedding space, not of the library.
    default_max_distance: float = 0.5
    # The weights files it runs (weights.ModelFile).
    models: tuple = ()

    def prepare(self):
        """Download and verify the weights; a no-op once done in this process.

        Raises weights.WeightsError when they cannot be had.
        """
        ensure_all(self.models)

    @abc.abstractmethod
    def detect(self, image):
        """The faces in *image* (RGB uint8, H x W x 3), as DetectedFace.

        Raises for an operational failure (weights missing, runtime error):
        the pipeline records nothing, and the photo stays pending.
        """

    @abc.abstractmethod
    def embed(self, aligned):
        """The embedding of *aligned*: a 112 x 112 RGB uint8 face, eyes level.

        Not normalized: the vector index scales it to unit length.
        """

    @abc.abstractmethod
    def health(self):
        """A BackendHealth: whether the weights are there and load."""

    def sharpness(self, aligned):
        """How sharp *aligned* is, 0 (a blur) to 1.

        Variance of the Laplacian on the grey face, the usual focus measure,
        scaled so an ordinary in-focus face reaches 1.
        """
        grey = aligned.astype(np.float32) @ np.array(
            [0.299, 0.587, 0.114], dtype=np.float32
        )
        laplacian = (
            grey[:-2, 1:-1]
            + grey[2:, 1:-1]
            + grey[1:-1, :-2]
            + grey[1:-1, 2:]
            - 4 * grey[1:-1, 1:-1]
        )
        return float(min(1.0, laplacian.var() / _SHARP_VARIANCE))


# Laplacian variance of a face that reads as sharp at 112 px. Measured on
# phone photos: in-focus faces sit well above it, motion blur well below.
_SHARP_VARIANCE = 120.0
