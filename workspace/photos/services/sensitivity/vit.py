"""Falconsai's NSFW image classifier: a ViT-base fine-tuned on two classes,
"normal" and "nsfw" (2023, Apache-2.0).

Run from its ONNX export (onnx-community), int8-quantized: 87 MB of weights,
about 17 GFLOPs a photo at 224 px. Nudity is what it was trained on; a
medical photo or a gory one is marked by hand.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from workspace.files.sensitivity import BlurPreference

from ..detection import runtime
from ..detection.health import weights_health
from ..detection.weights import ModelFile
from .base import SensitiveBackend

_REPO = (
    "https://huggingface.co/onnx-community/nsfw_image_detection-ONNX/resolve/"
    "1ceb3c7fe1e9f3f2507e6df577437f23a9149fd5/onnx/"
)
FALCONSAI_VIT = ModelFile(
    name="nsfw_image_detection_int8.onnx",
    sha256="bdc941491cd09ae75c5f40b038a5cd64482fa4e2b5297747323c5cda31303920",
    url=_REPO + "model_int8.onnx",
)

# The model's preprocessing (preprocessor_config.json): squashed to 224 x 224
# without keeping the aspect ratio, bilinear, scaled to [-1, 1].
_INPUT_SIZE = 224
_NSFW = 1


class FalconsaiVitBackend(SensitiveBackend):
    key = "falconsai_vit"
    models = (FALCONSAI_VIT,)
    # Twice the input: the short side of a photo up to 2:1 is squashed down
    # to it rather than stretched up.
    decode_size = 2 * _INPUT_SIZE
    # Standard is the model's own decision boundary, the only one its card
    # evaluates (98% accuracy on its argmax, over 80,000 images it does not
    # publish). The score is not a calibrated probability, and the card does
    # not say what its "nsfw" class takes in. On 1,971 Open Images photos,
    # none of them explicit, 9 score 0.5 or more and 11 score 0.2 or more (a
    # bikini, a kiss, a mannequin...), the median 0.0001. The stricter steps
    # stay close above that: lower, they blur more photos of people for no
    # measured recall.
    thresholds = {
        BlurPreference.STRICT: 0.25,
        BlurPreference.HIGH: 0.35,
        BlurPreference.STANDARD: 0.5,
        BlurPreference.LOW: 0.9,
    }

    def score(self, image):
        resized = image.resize((_INPUT_SIZE, _INPUT_SIZE), Image.BILINEAR)
        pixels = np.asarray(resized, dtype=np.float32) / 127.5 - 1.0
        blob = pixels.transpose(2, 0, 1)[None]
        (logits,) = runtime.session(FALCONSAI_VIT).run(None, {"pixel_values": blob})
        logits = logits[0].astype(np.float64)
        odds = np.exp(logits - logits.max())
        return float(odds[_NSFW] / odds.sum())

    def health(self):
        return weights_health("Falconsai ViT", self.models)
