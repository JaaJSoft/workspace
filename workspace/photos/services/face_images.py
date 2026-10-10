"""Find the faces of one decoded picture: detect, align, score, embed, crop.

A photo is one picture; a video is the frames sampled from it (see
services/face_video.py). What becomes of the faces is face_analysis.py's.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from PIL import Image

from .detection.base import FaceBackend
from .detection.geometry import align

# Side of the square WebP shown for a face, in px.
CROP_SIZE = 160
_CROP_QUALITY = 80
# The crop takes in this much of the face's surroundings: hair, chin, ears.
_CROP_MARGIN = 1.6


@dataclass
class Detection:
    """One face of a picture, in the pixel coordinates of that picture."""

    box: tuple[float, float, float, float]
    landmarks: np.ndarray
    score: float
    # 0 to 1: FaceBackend.quality.
    quality: float
    # As the backend returned it, not normalized.
    embedding: np.ndarray
    # The square WebP shown for the face.
    crop: bytes
    # The picture's size, for the box as fractions of it.
    width: int
    height: int


def detect_faces(image, backend: FaceBackend):
    """The faces of *image* (RGB uint8), the most confident first.

    Faces too small to tell anyone apart are dropped, and at most
    PHOTOS_FACES_MAX_PER_PHOTO are kept.
    """
    backend.prepare()
    height, width = image.shape[:2]
    detected = [
        face
        for face in backend.detect(image)
        if min(face.box[2], face.box[3]) >= settings.PHOTOS_FACES_MIN_SIZE
    ]
    detected.sort(key=lambda face: face.score, reverse=True)
    found = []
    for detection in detected[: settings.PHOTOS_FACES_MAX_PER_PHOTO]:
        aligned = align(image, detection.landmarks)
        embedding = backend.embed(aligned)
        found.append(
            Detection(
                box=detection.box,
                landmarks=detection.landmarks,
                score=detection.score,
                quality=backend.quality(detection, aligned, embedding),
                embedding=embedding,
                crop=_crop(image, detection.box),
                width=width,
                height=height,
            )
        )
    return found


def _crop(image, box):
    """The square WebP of a face and some of its surroundings."""
    height, width = image.shape[:2]
    x, y, w, h = box
    side = min(max(w, h) * _CROP_MARGIN, width, height)
    left = min(max(0.0, x + w / 2 - side / 2), width - side)
    top = min(max(0.0, y + h / 2 - side / 2), height - side)
    square = Image.fromarray(image).crop(
        (round(left), round(top), round(left + side), round(top + side))
    )
    square = square.resize((CROP_SIZE, CROP_SIZE), Image.LANCZOS)
    buffer = io.BytesIO()
    square.save(buffer, format="WEBP", quality=_CROP_QUALITY)
    return buffer.getvalue()
