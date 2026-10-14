"""The interface every sensitive-content detector implements.

A detector scores a photo from 0 (nothing to hide) to 1 (explicit): the
pipeline (services/sensitive_photos.py) blurs it from the threshold on. It
says nothing about who or what is in the picture, only whether it should be
kept off a screen others may see.
"""

from __future__ import annotations

import abc

from ..detection.weights import ensure_all


class SensitiveBackend(abc.ABC):
    """A sensitive-content detector."""

    # The registry key (PHOTOS_SENSITIVE_BACKEND), recorded with every score:
    # a photo scored by another detector counts as pending again.
    key: str = ""
    # The weights files it runs (detection.weights.ModelFile).
    models: tuple = ()
    # Longest side, in px, of the copy of the photo handed to score.
    decode_size: int = 224
    # The default score from which a photo is blurred, overridden by
    # PHOTOS_SENSITIVE_THRESHOLD. Tuned for recall: a photo blurred by mistake
    # costs a click, an explicit one shown in clear costs much more.
    threshold: float = 0.5

    def prepare(self):
        """Download and verify the weights; a no-op once done in this process.

        Raises detection.weights.WeightsError when they cannot be had.
        """
        ensure_all(self.models)

    @abc.abstractmethod
    def score(self, image):
        """How likely *image* is to be explicit, from 0 to 1.

        *image* is a PIL RGB image as displayed (EXIF orientation applied),
        at most ``decode_size`` on its longest side. Raises for an
        operational failure (weights missing): the pipeline records nothing,
        and the photo stays pending.
        """

    @abc.abstractmethod
    def health(self):
        """A BackendHealth: whether the backend can run, for the dashboard."""
