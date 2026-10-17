"""The local backends: image and text encoders run on the CPU by fastembed.

fastembed (Qdrant) carries the whole pipeline of each model: the ONNX exports
of its two encoders, its own preprocessing and tokenizer, and the
onnxruntime sessions. The weights download from the Hugging Face Hub on first
use, into PHOTOS_MODEL_DIR/fastembed (``manage.py download_embedding_models``
fetches them ahead of time).

Which one to run is a trade between quality, CPU and memory: the embedding
bench (scripts/embedding_bench.py, docs/photos/embedding-bench.md) measures
all three on the same photos, and calibrates each model's thresholds.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings

from ..detection.base import BackendHealth
from .base import EmbeddingBackend

_models = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Encoder:
    """One encoder of a model: the name fastembed knows it by, and where the
    Hub keeps its ONNX file (read by health(), without importing fastembed)."""

    name: str
    repo: str
    file: str
    image: bool


def cache_dir():
    return str(Path(settings.PHOTOS_MODEL_DIR) / "fastembed")


def _build(encoder, **kwargs):
    # Imported here: the web process never runs a model, and fastembed pulls
    # onnxruntime and the Hub client in.
    from fastembed import ImageEmbedding, TextEmbedding

    model_class = ImageEmbedding if encoder.image else TextEmbedding
    return model_class(
        encoder.name,
        cache_dir=cache_dir(),
        threads=settings.PHOTOS_ONNX_THREADS,
        providers=["CPUExecutionProvider"],
        **kwargs,
    )


def model(encoder):
    """The fastembed model of *encoder*, downloading it if needed, built once
    per process: a session costs its weights in memory and seconds to build."""
    with _lock:
        cached = _models.get(encoder)
        if cached is None:
            cached = _models[encoder] = _build(encoder)
        return cached


def is_cached(encoder):
    """Whether *encoder*'s ONNX file is in the cache fastembed downloads to,
    read off the Hub cache layout: models--<org>--<repo>/snapshots/<rev>/."""
    repo = Path(cache_dir()) / f"models--{encoder.repo.replace('/', '--')}"
    return any(repo.glob(f"snapshots/*/{encoder.file}"))


class LocalBackend(EmbeddingBackend):
    """A model fastembed runs: its two encoders and how to name it."""

    label = ""
    image_encoder: Encoder
    text_encoder: Encoder

    def prepare(self):
        model(self.image_encoder)
        model(self.text_encoder)

    def embed_image(self, image):
        (vector,) = model(self.image_encoder).embed([image])
        return vector

    def embed_text(self, text):
        (vector,) = model(self.text_encoder).embed([text])
        return vector

    def health(self):
        """Answered from the files on disk, importing neither fastembed nor
        the Hub client: the dashboard renders in the web process, which never
        runs a model."""
        if find_spec("fastembed") is None or find_spec("onnxruntime") is None:
            return BackendHealth(ok=False, detail="fastembed is not installed")
        if not all(map(is_cached, (self.image_encoder, self.text_encoder))):
            return BackendHealth(
                ok=True, detail=f"{self.label}, weights download on first use"
            )
        return BackendHealth(ok=True, detail=f"{self.label}, ready")


class ClipVitB32Backend(LocalBackend):
    """OpenAI CLIP ViT-B/32 (2021, MIT): the cheapest of the CLIP family,
    about 4.4 GFLOPs a photo, 340 MB + 250 MB of weights. English only."""

    key = "clip_vit_b32"
    label = "CLIP ViT-B/32"
    dims = 512
    image_encoder = Encoder(
        "Qdrant/clip-ViT-B-32-vision",
        "Qdrant/clip-ViT-B-32-vision",
        "model.onnx",
        image=True,
    )
    text_encoder = Encoder(
        "Qdrant/clip-ViT-B-32-text",
        "Qdrant/clip-ViT-B-32-text",
        "model.onnx",
        image=False,
    )
    # Calibrated by the embedding bench (docs/photos/embedding-bench.md):
    # `description` is the best F1, `duplicate` the tightest that still finds
    # 95% of the copies: looser, a library shot in bursts gets photos taken
    # days apart as near-duplicates. `similar` stays tighter than its best F1
    # (70 photos at 46% precision): a "more like this" row is judged on what
    # it shows, not on what it misses.
    similar_max_distance = 0.25
    duplicate_max_distance = 0.075
    description_max_distance = 0.765
    # On the face bench's libraries, 25% of the torsos with a named person
    # nearby get a suggestion, right 98% of the time (88% on the smaller
    # libraries alone).
    appearance_max_distance = 0.12
    appearance_margin = 0.08
    tag_thresholds = {
        "dog": 0.759,
        "cat": 0.737,
        "horse": 0.714,
        "mountain": 0.742,
        "snow": 0.75,
        "sunset": 0.751,
        "garden": 0.733,
        "car": 0.747,
        "cake": 0.74,
        "cocktail": 0.723,
        "wedding": 0.729,
        "christmas": 0.742,
        "swimming pool": 0.726,
    }
    # A video's tags, from the closest of its key frames: fitted the same way
    # on the photos filmed and read back as key frames, the photo table's
    # tags only.
    video_tag_thresholds = {
        "dog": 0.745,
        "cat": 0.733,
        "horse": 0.745,
        "mountain": 0.735,
        "snow": 0.745,
        "sunset": 0.753,
        "garden": 0.745,
        "cake": 0.743,
        "cocktail": 0.713,
    }


class Siglip2BaseBackend(LocalBackend):
    """Google SigLIP 2 base, patch 16, 224 px (2025, Apache-2.0): better
    retrieval than CLIP and multilingual, a query typed in French included.
    About four times CLIP ViT-B/32's CPU per photo; its text encoder carries
    a 256k-token vocabulary, 1.1 GB of weights."""

    key = "siglip2_base"
    label = "SigLIP 2 base"
    dims = 768
    image_encoder = Encoder(
        "google/siglip2-base-patch16-224",
        "onnx-community/siglip2-base-patch16-224-ONNX",
        "onnx/vision_model.onnx",
        image=True,
    )
    text_encoder = Encoder(
        "google/siglip2-base-patch16-224",
        "onnx-community/siglip2-base-patch16-224-ONNX",
        "onnx/text_model.onnx",
        image=False,
    )
    # Trained with a sigmoid loss: a text and the photo it describes meet at
    # a much lower cosine similarity than with CLIP, around 0.1. Calibrated
    # like CLIP's.
    similar_max_distance = 0.3
    duplicate_max_distance = 0.07
    description_max_distance = 0.92
    # On the face bench's libraries, 36% of the torsos with a named person
    # nearby get a suggestion, right 97% of the time (94% on the smaller
    # libraries alone).
    appearance_max_distance = 0.15
    appearance_margin = 0.08
    tag_thresholds = {
        "dog": 0.926,
        "cat": 0.901,
        "horse": 0.888,
        "beach": 0.899,
        "mountain": 0.902,
        "snow": 0.895,
        "sea": 0.89,
        "forest": 0.904,
        "lake": 0.887,
        "sunset": 0.911,
        "garden": 0.906,
        "night": 0.889,
        "car": 0.918,
        "boat": 0.884,
        "bicycle": 0.91,
        "cake": 0.899,
        "cocktail": 0.903,
        "child": 0.881,
        "wedding": 0.896,
        "christmas": 0.897,
        "skiing": 0.869,
        "swimming pool": 0.888,
        "screenshot": 0.885,
        "document": 0.901,
    }
    video_tag_thresholds = {
        "dog": 0.925,
        "cat": 0.9,
        "horse": 0.889,
        "beach": 0.901,
        "mountain": 0.896,
        "snow": 0.888,
        "sea": 0.894,
        "forest": 0.902,
        "sunset": 0.906,
        "garden": 0.905,
        "night": 0.887,
        "car": 0.925,
        "boat": 0.882,
        "bicycle": 0.914,
        "cake": 0.896,
        "cocktail": 0.9,
        "wedding": 0.892,
        "christmas": 0.895,
        "skiing": 0.872,
        "swimming pool": 0.885,
        "document": 0.901,
    }


class JinaClipV1Backend(LocalBackend):
    """Jina CLIP v1 (2024, Apache-2.0): a CLIP-style model whose text encoder
    reads long descriptions as well as short queries. English only."""

    key = "jina_clip_v1"
    label = "Jina CLIP v1"
    dims = 768
    image_encoder = Encoder(
        "jinaai/jina-clip-v1",
        "jinaai/jina-clip-v1",
        "onnx/vision_model.onnx",
        image=True,
    )
    text_encoder = Encoder(
        "jinaai/jina-clip-v1",
        "jinaai/jina-clip-v1",
        "onnx/text_model.onnx",
        image=False,
    )
    # Calibrated like CLIP's.
    similar_max_distance = 0.3
    duplicate_max_distance = 0.085
    description_max_distance = 0.89
    # On the face bench's libraries, 29% of the torsos with a named person
    # nearby get a suggestion, right 98% of the time (93% on the smaller
    # libraries alone).
    appearance_max_distance = 0.14
    appearance_margin = 0.08
    tag_thresholds = {
        "dog": 0.889,
        "cat": 0.84,
        "horse": 0.816,
        "beach": 0.834,
        "mountain": 0.86,
        "snow": 0.865,
        "forest": 0.827,
        "lake": 0.834,
        "sunset": 0.828,
        "garden": 0.843,
        "car": 0.879,
        "boat": 0.822,
        "airplane": 0.824,
        "bicycle": 0.837,
        "cake": 0.845,
        "cocktail": 0.821,
        "selfie": 0.816,
        "wedding": 0.848,
        "skiing": 0.837,
        "swimming pool": 0.818,
        "screenshot": 0.863,
        "document": 0.853,
    }
    video_tag_thresholds = {
        "dog": 0.887,
        "cat": 0.843,
        "horse": 0.821,
        "beach": 0.844,
        "mountain": 0.861,
        "snow": 0.852,
        "forest": 0.823,
        "lake": 0.842,
        "sunset": 0.828,
        "garden": 0.855,
        "car": 0.879,
        "boat": 0.83,
        "airplane": 0.821,
        "bicycle": 0.841,
        "cake": 0.834,
        "cocktail": 0.82,
        "selfie": 0.824,
        "wedding": 0.841,
        "skiing": 0.837,
        "swimming pool": 0.811,
        "document": 0.844,
    }
