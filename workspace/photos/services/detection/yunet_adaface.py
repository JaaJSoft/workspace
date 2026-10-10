"""YuNet to detect, AdaFace to embed (mk-minchul/AdaFace, IR-50).

AdaFace trains on each face with a margin that adapts to how recognizable it
is, which keeps its embeddings steadier than SFace's on the small, blurred and
turned faces of a real library. It is the yunet_sface pipeline with a better
embedding: the same detector, 512-d vectors, and on the face bench nearly
ArcFace's grouping for a quarter of its CPU. The IR-18 network, half the
size, groups little better than SFace there.

The weights are the authors' IR-50 trained on WebFace4M, exported to ONNX by
yakhyo/adaface-onnx; both repositories are MIT. WebFace4M itself is licensed
for non-commercial research only, and whether that reaches a model trained on
it is not settled: an instance run by or for a company should not enable this
backend without checking.
"""

from __future__ import annotations

import numpy as np

from . import runtime
from .base import FaceBackend
from .health import weights_health
from .weights import ModelFile
from .yunet_sface import YUNET, yunet_faces

ADAFACE = ModelFile(
    name="adaface_ir_50.onnx",
    sha256="9c0ae385d7362323c92d218de598d32e52a9130b78faac6a8a924091aca62c0b",
    url=(
        "https://github.com/yakhyo/adaface-onnx/releases/download/weights/"
        "adaface_ir_50.onnx"
    ),
)

# The export divides the features by their length before returning them;
# this is the value it divides.
_FEATURES = "/model/output_layer/output_layer.4/BatchNormalization_output_0"


class YuNetAdaFaceBackend(FaceBackend):
    key = "yunet_adaface"
    dims = 512
    # The face bench's knee: looser barely saves a merge and puts more faces
    # in the wrong group (docs/photos/face-bench.md).
    default_max_distance = 0.6
    models = (YUNET, ADAFACE)

    def detect(self, image):
        return yunet_faces(image)

    def embed(self, aligned):
        # AdaFace takes BGR, scaled to -1..1, NCHW.
        blob = ((aligned[:, :, ::-1].astype(np.float32) - 127.5) / 127.5).transpose(
            2, 0, 1
        )[None]
        recognizer = runtime.session(ADAFACE, expose=(_FEATURES,))
        _unit, features = recognizer.run(None, {"input": blob})
        return features.reshape(-1)

    def health(self):
        return weights_health(
            "YuNet + AdaFace (trained on non-commercial data)", self.models
        )
