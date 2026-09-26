"""Photos module: face detection and grouping."""

import os
from pathlib import Path

from .env import env_bool
from .storage import MEDIA_ROOT

# Faces are detected and grouped only where the instance allows it AND the
# user turned it on (the `photos` / `faces_enabled` user setting). Off by
# default: a face embedding is biometric data, and detection costs a CPU the
# Raspberry Pi target may not have to spare.
PHOTOS_FACES_ENABLED = env_bool("PHOTOS_FACES_ENABLED", False)

# Which detection backend runs (photos/services/detection/registry.py):
# "yunet_sface" (default, permissive licences, 128-d) or "scrfd_arcface"
# (InsightFace buffalo_l, 512-d, weights licensed for non-commercial research
# only). Switching changes the embedding space: see docs/photos/README.md.
PHOTOS_FACE_BACKEND = os.getenv("PHOTOS_FACE_BACKEND", "yunet_sface")

# Where model weights are downloaded on first use, each checked against a
# pinned sha256. Under MEDIA_ROOT by default, so a container keeps them on its
# data volume; the Dockerfile's `face-models` stage bakes them into the image
# and points this elsewhere (empty counts as unset).
PHOTOS_MODEL_DIR = os.getenv("PHOTOS_MODEL_DIR") or str(Path(MEDIA_ROOT) / "models")

# Threads per onnxruntime session. 1 by default so several Celery worker
# processes on one host do not each claim every core.
PHOTOS_ONNX_THREADS = int(os.getenv("PHOTOS_ONNX_THREADS", "1"))

# Longest side, in px, of the copy of the original that detection runs on.
# The 512 px thumbnails are too small for the faces of a group photo.
PHOTOS_FACES_DECODE_SIZE = int(os.getenv("PHOTOS_FACES_DECODE_SIZE", "1600"))
# Originals larger than this are not read for faces at all.
PHOTOS_FACES_MAX_FILE_BYTES = int(
    os.getenv("PHOTOS_FACES_MAX_FILE_BYTES", str(64 * 1024 * 1024))
)
# Faces whose shorter side is under this many px (at the decode size) are
# dropped: too small to tell anyone apart.
PHOTOS_FACES_MIN_SIZE = int(os.getenv("PHOTOS_FACES_MIN_SIZE", "24"))
# The most faces kept per photo, the most confident first (a stadium crowd
# would otherwise fill a library with strangers).
PHOTOS_FACES_MAX_PER_PHOTO = int(os.getenv("PHOTOS_FACES_MAX_PER_PHOTO", "40"))

# Cosine distance under which two faces count as the same person. Empty means
# the backend's own default, which is calibrated to its embedding space.
PHOTOS_FACES_MAX_DISTANCE = (
    float(os.environ["PHOTOS_FACES_MAX_DISTANCE"])
    if os.getenv("PHOTOS_FACES_MAX_DISTANCE")
    else None
)
# Once a user has this many ungrouped faces, a clustering run is queued
# without waiting for the nightly one.
PHOTOS_FACES_CLUSTER_PENDING = int(os.getenv("PHOTOS_FACES_CLUSTER_PENDING", "50"))
