"""The default backend: YuNet to detect, SFace to embed (OpenCV Zoo).

Both models are MIT / Apache-2.0 licensed, small (0.2 MB and 37 MB) and run
comfortably on a CPU. Their embeddings are 128-d. The post-processing is
OpenCV's FaceDetectorYN and FaceRecognizerSF, rewritten in numpy.
"""

from __future__ import annotations

import numpy as np

from . import runtime
from .base import DetectedFace, FaceBackend
from .geometry import letterbox, nms
from .health import weights_health
from .weights import ModelFile

YUNET = ModelFile(
    name="face_detection_yunet_2023mar.onnx",
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    url=(
        "https://github.com/opencv/opencv_zoo/raw/"
        "f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx"
    ),
)
SFACE = ModelFile(
    name="face_recognition_sface_2021dec.onnx",
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    url=(
        "https://github.com/opencv/opencv_zoo/raw/"
        "ba91a3b91d00d76e86540d4013f944bd6b514e39/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx"
    ),
)

# The model's input is fixed at 640 x 640.
_INPUT_SIZE = 640
_STRIDES = (8, 16, 32)
# OpenCV Zoo's defaults for this model.
_SCORE_THRESHOLD = 0.8
_NMS_THRESHOLD = 0.3


class YuNetSFaceBackend(FaceBackend):
    key = "yunet_sface"
    dims = 128
    # OpenCV's recommended same-identity threshold for SFace is a cosine
    # similarity of 0.363; grouping a library wants fewer false merges than a
    # one-off verification, so the distance is a little tighter.
    default_max_distance = 0.58
    models = (YUNET, SFACE)

    def detect(self, image):
        square, scale = letterbox(image, _INPUT_SIZE)
        # YuNet takes BGR, 0-255, NCHW.
        blob = square[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32)
        names = _output_names()
        outputs = runtime.session(YUNET).run(names, {"input": blob})
        named = dict(zip(names, outputs, strict=True))

        boxes, landmarks, scores = [], [], []
        for stride in _STRIDES:
            cols = _INPUT_SIZE // stride
            cls = np.clip(named[f"cls_{stride}"].reshape(-1), 0, 1)
            obj = np.clip(named[f"obj_{stride}"].reshape(-1), 0, 1)
            score = np.sqrt(cls * obj)
            keep = score >= _SCORE_THRESHOLD
            if not keep.any():
                continue
            index = np.nonzero(keep)[0]
            col = (index % cols).astype(np.float32)
            row = (index // cols).astype(np.float32)
            bbox = named[f"bbox_{stride}"].reshape(-1, 4)[index]
            kps = named[f"kps_{stride}"].reshape(-1, 10)[index]
            cx = (col + bbox[:, 0]) * stride
            cy = (row + bbox[:, 1]) * stride
            w = np.exp(bbox[:, 2]) * stride
            h = np.exp(bbox[:, 3]) * stride
            boxes.append(np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1))
            points = kps.reshape(-1, 5, 2)
            points = np.stack(
                [
                    (points[:, :, 0] + col[:, None]) * stride,
                    (points[:, :, 1] + row[:, None]) * stride,
                ],
                axis=2,
            )
            landmarks.append(points)
            scores.append(score[index])
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
        # SFace takes RGB, 0-255, NCHW (OpenCV swaps its BGR crop to RGB).
        blob = aligned.transpose(2, 0, 1)[None].astype(np.float32)
        (features,) = runtime.session(SFACE).run(None, {"data": blob})
        return features.reshape(-1)

    def health(self):
        return weights_health("YuNet + SFace", self.models)


def _output_names():
    return [
        f"{kind}_{stride}"
        for kind in ("cls", "obj", "bbox", "kps")
        for stride in _STRIDES
    ]
