"""A deterministic backend for tests: CI never downloads weights.

A photo's vector is its mean colour, and a text's is the mean of the colour
words it holds: "red" finds the red pictures, two red pictures are
near-duplicates, a red and a dark red one are similar, a red and a blue one
are unrelated. Enough to exercise the whole pipeline (decoding, the vector
index, similarity, search by description) with images a test draws in a few
lines of Pillow.
"""

import numpy as np

from ..detection.base import BackendHealth
from .base import EmbeddingBackend

COLOURS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}
# Keeps a grey picture, whose centred colour is zero, a direction.
_BIAS = 0.1


def colour_vector(rgb, dims):
    """The vector of a picture whose mean colour is *rgb* (0 to 255)."""
    vector = np.zeros(dims)
    vector[:3] = np.asarray(rgb, dtype=np.float64) / 255.0 - 0.5
    vector[3] = _BIAS
    return vector


class FakeEmbeddingBackend(EmbeddingBackend):
    key = "fake"
    # The default backend's size: the vector index the migrations create.
    dims = 768
    similar_max_distance = 0.3
    duplicate_max_distance = 0.02
    description_max_distance = 0.5

    def embed_image(self, image):
        mean = np.asarray(image.convert("RGB"), dtype=np.float64).mean(axis=(0, 1))
        return colour_vector(mean, self.dims)

    def embed_text(self, text):
        colours = [COLOURS[word] for word in text.lower().split() if word in COLOURS]
        if not colours:
            # Points away from every colour: matches nothing.
            vector = np.zeros(self.dims)
            vector[4] = 1.0
            return vector
        return colour_vector(np.mean(colours, axis=0), self.dims)

    def health(self):
        return BackendHealth(ok=True, detail="fake backend (tests only)")
