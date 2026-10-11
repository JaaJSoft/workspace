"""The default backend: OpenAI CLIP ViT-B/32, run on the CPU by fastembed.

fastembed (Qdrant) carries the whole local pipeline: the ONNX exports of the
image and text encoders (OpenAI's weights, MIT, 340 MB and 250 MB), CLIP's
own preprocessing and tokenizer, and the onnxruntime sessions. ViT-B/32 is the
cheapest of the CLIP family to run, about 4.4 GFLOPs a photo: a Raspberry Pi
4 gets through a library of twenty thousand photos in a night. Its
embeddings are 512-d.

The weights download from the Hugging Face Hub on first use, into
PHOTOS_MODEL_DIR/fastembed (``manage.py download_embedding_models`` fetches
them ahead of time).
"""

from __future__ import annotations

import threading
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings

from ..detection.base import BackendHealth
from .base import EmbeddingBackend

IMAGE_MODEL = "Qdrant/clip-ViT-B-32-vision"
TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"

_models = {}
_lock = threading.Lock()


def cache_dir():
    return str(Path(settings.PHOTOS_MODEL_DIR) / "fastembed")


def _build(name, **kwargs):
    # Imported here: the web process never runs a model, and fastembed pulls
    # onnxruntime and the Hub client in.
    from fastembed import ImageEmbedding, TextEmbedding

    model_class = ImageEmbedding if name == IMAGE_MODEL else TextEmbedding
    return model_class(
        name,
        cache_dir=cache_dir(),
        threads=settings.PHOTOS_ONNX_THREADS,
        providers=["CPUExecutionProvider"],
        **kwargs,
    )


def model(name):
    """The fastembed model *name*, downloading it if needed, built once per
    process: a session costs its weights in memory and seconds to build."""
    with _lock:
        cached = _models.get(name)
        if cached is None:
            cached = _models[name] = _build(name)
        return cached


class ClipVitB32Backend(EmbeddingBackend):
    key = "clip_vit_b32"
    dims = 512
    # Shots of one burst sit above a cosine similarity of 0.94; photos of the
    # same scene or subject above 0.75. A query text and a photo it describes
    # reach 0.2 to 0.35, far below any two photos: the two thresholds are of
    # different spaces in practice.
    similar_max_distance = 0.25
    duplicate_max_distance = 0.06
    description_max_distance = 0.8

    def prepare(self):
        model(IMAGE_MODEL)
        model(TEXT_MODEL)

    def embed_image(self, image):
        (vector,) = model(IMAGE_MODEL).embed([image])
        return vector

    def embed_text(self, text):
        (vector,) = model(TEXT_MODEL).embed([text])
        return vector

    def health(self):
        """Answered from the files on disk, importing neither fastembed nor
        the Hub client: the dashboard renders in the web process, which never
        runs a model."""
        if find_spec("fastembed") is None or find_spec("onnxruntime") is None:
            return BackendHealth(ok=False, detail="fastembed is not installed")
        if not all(_cached(name) for name in (IMAGE_MODEL, TEXT_MODEL)):
            return BackendHealth(
                ok=True, detail="CLIP ViT-B/32, weights download on first use"
            )
        return BackendHealth(ok=True, detail="CLIP ViT-B/32, ready")


def _cached(name):
    """Whether *name*'s ONNX file is in the cache fastembed downloads to, read
    off the Hub cache layout: models--<org>--<repo>/snapshots/<rev>/."""
    repo = Path(cache_dir()) / f"models--{name.replace('/', '--')}"
    return any(repo.glob("snapshots/*/model.onnx"))
