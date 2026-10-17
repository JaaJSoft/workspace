"""Find the people in a picture: NanoDet-Plus (OpenCV Zoo), COCO's person class.

Apache-2.0, 3.8 MB, run at 416 x 416 on the CPU. It finds the people a face
detector cannot: seen from behind, turned away, too far for a face. The
post-processing is OpenCV Zoo's nanodet.py, rewritten in numpy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import runtime
from .geometry import letterbox, nms
from .weights import ModelFile

NANODET = ModelFile(
    name="object_detection_nanodet_2022nov.onnx",
    sha256="4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186",
    url=(
        "https://github.com/opencv/opencv_zoo/raw/"
        "47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet/"
        "object_detection_nanodet_2022nov.onnx"
    ),
)

# The model's input is fixed at 416 x 416.
_INPUT_SIZE = 416
_STRIDES = (8, 16, 32)
# Each box side is a distribution over this many + 1 bins of one stride.
_REG_MAX = 7
_BINS = np.arange(_REG_MAX + 1, dtype=np.float32)
# BGR, as OpenCV Zoo feeds it.
_MEAN = np.array([103.53, 116.28, 123.675], dtype=np.float32)
_STD = np.array([57.375, 57.12, 58.395], dtype=np.float32)
_PERSON = 0
_CLASSES = 80
_SCORE_THRESHOLD = 0.4
_NMS_THRESHOLD = 0.6


@dataclass(frozen=True)
class DetectedPerson:
    """One person found in an image, in that image's pixel coordinates."""

    # x, y, width, height of the box.
    box: tuple[float, float, float, float]
    # The detector's confidence, 0 to 1.
    score: float


def detect_people(image):
    """The people in *image* (RGB uint8, H x W x 3), as DetectedPerson."""
    square, scale = letterbox(image, _INPUT_SIZE)
    blob = (square[:, :, ::-1].astype(np.float32) - _MEAN) / _STD
    blob = blob.transpose(2, 0, 1)[None]
    session = runtime.session(NANODET)
    outputs = session.run(None, {session.get_inputs()[0].name: blob})
    # The export names its outputs by graph node: told apart by their shape,
    # classes then box distributions, the finest stride (most anchors) first.
    by_size = sorted(outputs, key=lambda out: -out.shape[1])
    scores = [out[0] for out in by_size if out.shape[2] == _CLASSES]
    distributions = [out[0] for out in by_size if out.shape[2] != _CLASSES]

    boxes, kept_scores = [], []
    for stride, score, distribution in zip(
        _STRIDES, scores, distributions, strict=True
    ):
        person = score[:, _PERSON]
        index = np.nonzero(person >= _SCORE_THRESHOLD)[0]
        if not index.size:
            continue
        cols = _INPUT_SIZE // stride
        cx = (index % cols) * stride + (stride - 1) / 2
        cy = (index // cols) * stride + (stride - 1) / 2
        bins = distribution[index].reshape(-1, 4, _REG_MAX + 1)
        bins = np.exp(bins - bins.max(axis=2, keepdims=True))
        sides = (bins / bins.sum(axis=2, keepdims=True)) @ _BINS * stride
        boxes.append(
            np.stack(
                [
                    cx - sides[:, 0],
                    cy - sides[:, 1],
                    cx + sides[:, 2],
                    cy + sides[:, 3],
                ],
                axis=1,
            )
        )
        kept_scores.append(person[index])
    if not boxes:
        return []
    boxes = np.clip(np.concatenate(boxes), 0, _INPUT_SIZE) / scale
    kept_scores = np.concatenate(kept_scores)
    height, width = image.shape[:2]
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height)
    return [
        DetectedPerson(
            box=(
                float(boxes[i, 0]),
                float(boxes[i, 1]),
                float(boxes[i, 2] - boxes[i, 0]),
                float(boxes[i, 3] - boxes[i, 1]),
            ),
            score=float(kept_scores[i]),
        )
        for i in nms(boxes, kept_scores, _NMS_THRESHOLD)
    ]
