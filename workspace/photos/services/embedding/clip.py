"""The default backend: OpenAI CLIP ViT-B/32, run on the CPU.

The weights are OpenAI's (MIT), exported to ONNX by Jina AI's clip-as-service
(an image and a text encoder, 351 MB and 254 MB in float32); the tokenizer's
merges file is OpenAI's own. ViT-B/32 is the cheapest of the CLIP family to
run, about 4.4 GFLOPs a photo: a Raspberry Pi 4 gets through a library of
twenty thousand photos in a night. Its embeddings are 512-d.
"""

from __future__ import annotations

import threading

import numpy as np
from PIL import Image

from ..detection import runtime, weights
from ..detection.health import weights_health
from .base import EmbeddingBackend
from .tokenizer import ClipTokenizer

_ONNX = (
    "https://clip-as-service.s3.us-east-2.amazonaws.com/"
    "models-436c69702d61732d53657276696365/onnx/ViT-B-32/"
)
VISUAL = weights.ModelFile(
    name="clip_vit_b32/visual.onnx",
    sha256="06395063c0a5c28b1a8d4bd585261501a878c8f52d1216db6c4cbb651f7c13f1",
    url=_ONNX + "visual.onnx",
)
TEXTUAL = weights.ModelFile(
    name="clip_vit_b32/textual.onnx",
    sha256="0af04c287a3be2570eaef7a1ef896d81c1989602df67a8905941afed589e545e",
    url=_ONNX + "textual.onnx",
)
MERGES = weights.ModelFile(
    name="clip_vit_b32/bpe_simple_vocab_16e6.txt.gz",
    sha256="924691ac288e54409236115652ad4aa250f48203de50a9e4722a6ecd48d6804a",
    url=(
        "https://github.com/openai/CLIP/raw/"
        "d05afc436d78f1c48dc0dbf8e5980a9d471f35f6/clip/bpe_simple_vocab_16e6.txt.gz"
    ),
)

# The model's input is a 224 x 224 square, normalized with the statistics of
# CLIP's training set.
_INPUT_SIZE = 224
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

_tokenizer = None
_tokenizer_lock = threading.Lock()


def _get_tokenizer():
    global _tokenizer
    with _tokenizer_lock:
        if _tokenizer is None:
            _tokenizer = ClipTokenizer.from_file(weights.ensure(MERGES))
        return _tokenizer


def pixel_values(image):
    """*image* as the image encoder reads it: its shorter side scaled to 224
    px (bicubic), the centre square cut out, normalized, 1 x 3 x 224 x 224."""
    width, height = image.size
    scale = _INPUT_SIZE / min(width, height)
    size = (
        max(_INPUT_SIZE, round(width * scale)),
        max(_INPUT_SIZE, round(height * scale)),
    )
    image = image.convert("RGB").resize(size, Image.BICUBIC)
    left = (size[0] - _INPUT_SIZE) // 2
    top = (size[1] - _INPUT_SIZE) // 2
    image = image.crop((left, top, left + _INPUT_SIZE, top + _INPUT_SIZE))
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    pixels = (pixels - _MEAN) / _STD
    return pixels.transpose(2, 0, 1)[None]


class ClipVitB32Backend(EmbeddingBackend):
    key = "clip_vit_b32"
    dims = 512
    models = (VISUAL, TEXTUAL, MERGES)
    # Shots of one burst sit above a cosine similarity of 0.94; photos of the
    # same scene or subject above 0.75. A query text and a photo it describes
    # reach 0.2 to 0.35, far below any two photos: the two thresholds are of
    # different spaces in practice.
    similar_max_distance = 0.25
    duplicate_max_distance = 0.06
    description_max_distance = 0.8

    def embed_image(self, image):
        (features,) = runtime.session(VISUAL).run(
            None, {"pixel_values": pixel_values(image)}
        )
        return features.reshape(-1)

    def embed_text(self, text):
        input_ids, attention_mask = _get_tokenizer()(text)
        (features,) = runtime.session(TEXTUAL).run(
            None,
            {
                "input_ids": np.array([input_ids], dtype=np.int32),
                "attention_mask": np.array([attention_mask], dtype=np.int32),
            },
        )
        return features.reshape(-1)

    def health(self):
        return weights_health("CLIP ViT-B/32", self.models)
