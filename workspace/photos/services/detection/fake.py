"""A deterministic backend for tests: CI never downloads weights.

A "face" is a patch of one saturated colour on a neutral background, and the
colour is the identity: two red squares are the same person, a red and a blue
one are two people. That is enough to exercise the whole pipeline (boxes,
landmarks, alignment, crops, the vector index, grouping) with images a test
draws in a few lines of Pillow.
"""

import hashlib

import numpy as np

from .base import BackendHealth, DetectedFace, FaceBackend

# Channels are quantized to this many levels, so JPEG-free test images with
# pure colours map to one code per patch.
_LEVELS = 4
# A patch smaller than this many pixels is noise, not a face.
_MIN_PIXELS = 64


class FakeFaceBackend(FaceBackend):
    key = "fake"
    dims = 128
    default_max_distance = 0.5

    def detect(self, image):
        codes = _colour_codes(image)
        faces = []
        for code in np.unique(codes[codes >= 0]):
            for x, y, w, h in _patches(codes == code):
                landmarks = np.array(
                    [
                        [x + 0.3 * w, y + 0.4 * h],
                        [x + 0.7 * w, y + 0.4 * h],
                        [x + 0.5 * w, y + 0.6 * h],
                        [x + 0.35 * w, y + 0.8 * h],
                        [x + 0.65 * w, y + 0.8 * h],
                    ]
                )
                faces.append(
                    DetectedFace(box=(x, y, w, h), landmarks=landmarks, score=0.99)
                )
        return faces

    def embed(self, aligned):
        centre = aligned[aligned.shape[0] // 2, aligned.shape[1] // 2]
        code = _colour_codes(centre[None, None])[0, 0]
        seed = int.from_bytes(hashlib.sha256(str(code).encode()).digest()[:8], "big")
        return np.random.default_rng(seed).standard_normal(self.dims)

    def sharpness(self, aligned):
        return 1.0

    def health(self):
        return BackendHealth(ok=True, detail="fake backend (tests only)")


def _patches(mask):
    """Boxes of the separate patches of *mask*, as x, y, width, height.

    Patches are told apart by the empty columns, then rows, between them:
    enough for the axis-aligned squares tests draw side by side.
    """
    boxes = []
    for x0, x1 in _runs(mask.any(axis=0)):
        for y0, y1 in _runs(mask[:, x0:x1].any(axis=1)):
            if mask[y0:y1, x0:x1].sum() >= _MIN_PIXELS:
                boxes.append((float(x0), float(y0), float(x1 - x0), float(y1 - y0)))
    return boxes


def _runs(flags):
    """(start, end) of each run of True in a 1-d boolean array."""
    padded = np.concatenate([[False], flags, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2], edges[1::2], strict=True))


def _colour_codes(image):
    """One code per pixel of a saturated colour, -1 for a neutral one."""
    quantized = image.astype(np.int32) * _LEVELS // 256
    spread = quantized.max(axis=-1) - quantized.min(axis=-1)
    codes = (
        quantized[..., 0] * _LEVELS * _LEVELS
        + quantized[..., 1] * _LEVELS
        + quantized[..., 2]
    )
    return np.where(spread >= 2, codes, -1)
