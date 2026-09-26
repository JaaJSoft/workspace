"""The opt-in backend: SCRFD to detect, ArcFace to embed (InsightFace buffalo_l).

More accurate than YuNet + SFace on hard faces (profiles, small faces in
group photos), and its 512-d embeddings separate look-alikes better. Not the
default because InsightFace licenses these pretrained weights for
NON-COMMERCIAL RESEARCH ONLY: an instance used by a company must not enable
it. The code here is ours; only the weights carry that licence.

The post-processing is insightface's SCRFD and ArcFaceONNX, rewritten in
numpy - the insightface package itself would pull opencv, scipy and
scikit-image in for these hundred lines.
"""

from __future__ import annotations

import numpy as np

from . import runtime
from .base import DetectedFace, FaceBackend
from .geometry import letterbox, nms
from .health import weights_health
from .weights import ModelFile

_BUFFALO_L = (
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
)
_BUFFALO_L_SHA256 = "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f"

SCRFD = ModelFile(
    name="buffalo_l/det_10g.onnx",
    sha256="5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    url=_BUFFALO_L,
    archive_member="det_10g.onnx",
    archive_sha256=_BUFFALO_L_SHA256,
)
ARCFACE = ModelFile(
    name="buffalo_l/w600k_r50.onnx",
    sha256="4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    url=_BUFFALO_L,
    archive_member="w600k_r50.onnx",
    archive_sha256=_BUFFALO_L_SHA256,
)

_INPUT_SIZE = 640
_STRIDES = (8, 16, 32)
_ANCHORS_PER_CELL = 2
# insightface's defaults for this model.
_SCORE_THRESHOLD = 0.5
_NMS_THRESHOLD = 0.4


class ScrfdArcFaceBackend(FaceBackend):
    key = "scrfd_arcface"
    dims = 512
    # insightface's usual verification threshold is a similarity around 0.4.
    default_max_distance = 0.6
    models = (SCRFD, ARCFACE)

    def detect(self, image):
        square, scale = letterbox(image, _INPUT_SIZE)
        blob = ((square.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        detector = runtime.session(SCRFD)
        outputs = detector.run(None, {detector.get_inputs()[0].name: blob})
        levels = len(_STRIDES)

        boxes, landmarks, scores = [], [], []
        for level, stride in enumerate(_STRIDES):
            score = outputs[level].reshape(-1)
            keep = np.nonzero(score >= _SCORE_THRESHOLD)[0]
            if not keep.size:
                continue
            distances = outputs[level + levels].reshape(-1, 4)[keep] * stride
            offsets = outputs[level + 2 * levels].reshape(-1, 10)[keep] * stride
            centers = _anchor_centers(stride)[keep]
            boxes.append(
                np.concatenate(
                    [centers - distances[:, :2], centers + distances[:, 2:]], 1
                )
            )
            landmarks.append(centers[:, None, :] + offsets.reshape(-1, 5, 2))
            scores.append(score[keep])
        if not boxes:
            return []
        boxes = np.concatenate(boxes) / scale
        landmarks = np.concatenate(landmarks) / scale
        scores = np.concatenate(scores)
        return [
            DetectedFace(
                box=(
                    float(boxes[i, 0]),
                    float(boxes[i, 1]),
                    float(boxes[i, 2] - boxes[i, 0]),
                    float(boxes[i, 3] - boxes[i, 1]),
                ),
                landmarks=landmarks[i],
                score=float(scores[i]),
            )
            for i in nms(boxes, scores, _NMS_THRESHOLD)
        ]

    def embed(self, aligned):
        blob = ((aligned.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        recognizer = runtime.session(ARCFACE)
        (features,) = recognizer.run(None, {recognizer.get_inputs()[0].name: blob})
        return features.reshape(-1)

    def health(self):
        return weights_health("SCRFD + ArcFace (non-commercial weights)", self.models)


def _anchor_centers(stride):
    """(x, y) of every anchor of a level, cell by cell, two anchors a cell."""
    cells = _INPUT_SIZE // stride
    ys, xs = np.mgrid[:cells, :cells]
    centers = np.stack([xs, ys], axis=-1).reshape(-1, 2).astype(np.float32) * stride
    return np.repeat(centers, _ANCHORS_PER_CELL, axis=0)
