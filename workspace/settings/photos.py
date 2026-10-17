"""Photos module: face detection and grouping, image embeddings."""

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
# "yunet_sface" (default, permissive licences, 128-d), "yunet_adaface" (512-d,
# MIT weights trained on a non-commercial dataset) or "scrfd_arcface"
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

# People found by their body as well as their face, and named after what they
# wear when their face was seen nearby: for the people seen from behind, too
# far or too blurred for a face. Needs face grouping and a local embedding
# backend (PHOTOS_EMBEDDINGS_ENABLED); costs each photo a person detector and
# one embedding per person. Off here for a host without the CPU to spare.
PHOTOS_APPEARANCES_ENABLED = env_bool("PHOTOS_APPEARANCES_ENABLED", True)

# Videos are read for faces from frames sampled at scene changes, and at least
# every PHOTOS_FACES_VIDEO_INTERVAL seconds (more sparsely in a long video, so
# the frames cover all of it), at most PHOTOS_FACES_VIDEO_MAX_FRAMES of them,
# decoded at PHOTOS_FACES_VIDEO_DECODE_SIZE px on the longest side. Videos
# longer (seconds) or larger than the limits are not read for faces at all.
PHOTOS_FACES_VIDEO_DECODE_SIZE = int(
    os.getenv("PHOTOS_FACES_VIDEO_DECODE_SIZE", "1280")
)
PHOTOS_FACES_VIDEO_INTERVAL = float(os.getenv("PHOTOS_FACES_VIDEO_INTERVAL", "2"))
PHOTOS_FACES_VIDEO_MAX_FRAMES = int(os.getenv("PHOTOS_FACES_VIDEO_MAX_FRAMES", "60"))
PHOTOS_FACES_VIDEO_MAX_DURATION = int(
    os.getenv("PHOTOS_FACES_VIDEO_MAX_DURATION", str(20 * 60))
)
PHOTOS_FACES_VIDEO_MAX_FILE_BYTES = int(
    os.getenv("PHOTOS_FACES_VIDEO_MAX_FILE_BYTES", str(2 * 1024**3))
)

# Videos are embedded and checked for sensitive content from their key frames,
# where ffmpeg is installed: at most PHOTOS_VIDEO_KEY_FRAMES_MAX frames are
# read, at the start, at scene changes and at a pace spreading half of them
# over the video. Videos longer (seconds) or larger than the limits are not
# read for either.
PHOTOS_VIDEO_KEY_FRAMES_MAX = int(os.getenv("PHOTOS_VIDEO_KEY_FRAMES_MAX", "12"))
PHOTOS_VIDEO_KEY_FRAMES_MAX_DURATION = int(
    os.getenv("PHOTOS_VIDEO_KEY_FRAMES_MAX_DURATION", str(20 * 60))
)
PHOTOS_VIDEO_KEY_FRAMES_MAX_FILE_BYTES = int(
    os.getenv("PHOTOS_VIDEO_KEY_FRAMES_MAX_FILE_BYTES", str(2 * 1024**3))
)

# Seconds a burst of additions to a shared album is gathered before its
# members are told, in one notification per contributor.
PHOTOS_ALBUM_NOTIFY_WINDOW_SECONDS = int(
    os.getenv("PHOTOS_ALBUM_NOTIFY_WINDOW_SECONDS", "60")
)

# Similar photos and search by description: one image embedding per photo, in
# a space the query text is embedded into too. Off by default: the default
# model costs 1.5 GB of weights and up to 2.2 GB of memory per worker process,
# and a remote backend sends every photo to another service.
PHOTOS_EMBEDDINGS_ENABLED = env_bool("PHOTOS_EMBEDDINGS_ENABLED", False)

# Which embedding backend runs (photos/services/embedding/registry.py):
# "siglip2_base" (default, Google SigLIP 2 on the CPU, multilingual,
# Apache-2.0, 768-d), "clip_vit_b32" (OpenAI CLIP ViT-B/32, MIT, 512-d: a
# quarter of the CPU and a third of the memory, for a small host, but poor in
# French), "jina_clip_v1" (Apache-2.0, 768-d) or "remote" (an
# OpenAI-compatible /embeddings endpoint serving a multimodal model).
# scripts/embedding_bench.py compares them. Switching changes the embedding
# space: see docs/photos/README.md.
PHOTOS_EMBEDDING_BACKEND = os.getenv("PHOTOS_EMBEDDING_BACKEND", "siglip2_base")

# The remote backend: the endpoint, the model it serves, and the size of the
# vectors that model returns (the vector index is built at that size). The key
# falls back to AI_API_KEY; none is sent when both are empty.
PHOTOS_EMBEDDING_BASE_URL = os.getenv("PHOTOS_EMBEDDING_BASE_URL", "")
PHOTOS_EMBEDDING_MODEL = os.getenv("PHOTOS_EMBEDDING_MODEL", "")
PHOTOS_EMBEDDING_API_KEY = os.getenv("PHOTOS_EMBEDDING_API_KEY", "")
PHOTOS_EMBEDDING_DIMS = int(os.getenv("PHOTOS_EMBEDDING_DIMS", "512"))
# Longest side, in px, of the JPEG copy sent to the remote backend: never the
# original.
PHOTOS_EMBEDDING_REMOTE_IMAGE_SIZE = int(
    os.getenv("PHOTOS_EMBEDDING_REMOTE_IMAGE_SIZE", "512")
)
PHOTOS_EMBEDDING_TIMEOUT = float(os.getenv("PHOTOS_EMBEDDING_TIMEOUT", "30"))
# Originals larger than this are not embedded.
PHOTOS_EMBEDDING_MAX_FILE_BYTES = int(
    os.getenv("PHOTOS_EMBEDDING_MAX_FILE_BYTES", str(64 * 1024 * 1024))
)
# Seconds a search waits for the worker to embed its text before giving up.
PHOTOS_EMBEDDING_QUERY_TIMEOUT = float(
    os.getenv("PHOTOS_EMBEDDING_QUERY_TIMEOUT", "15")
)
# Cosine distances, empty meaning the backend's own default (calibrated to its
# embedding space): under SIMILAR a photo is "similar", under DUPLICATE a
# near-duplicate, and a photo further than DESCRIPTION from a query text does
# not match it.
PHOTOS_SIMILAR_MAX_DISTANCE = (
    float(os.environ["PHOTOS_SIMILAR_MAX_DISTANCE"])
    if os.getenv("PHOTOS_SIMILAR_MAX_DISTANCE")
    else None
)
PHOTOS_DUPLICATE_MAX_DISTANCE = (
    float(os.environ["PHOTOS_DUPLICATE_MAX_DISTANCE"])
    if os.getenv("PHOTOS_DUPLICATE_MAX_DISTANCE")
    else None
)
PHOTOS_DESCRIPTION_MAX_DISTANCE = (
    float(os.environ["PHOTOS_DESCRIPTION_MAX_DISTANCE"])
    if os.getenv("PHOTOS_DESCRIPTION_MAX_DISTANCE")
    else None
)

# Sensitive photos (nudity) detected on analysis and blurred wherever they
# show as a thumbnail. Off by default, like the other models: it downloads
# 87 MB of weights and costs every photo one more pass in the worker. Photos
# can always be marked by hand.
PHOTOS_SENSITIVE_DETECTION_ENABLED = env_bool(
    "PHOTOS_SENSITIVE_DETECTION_ENABLED", False
)
# Which detector runs (photos/services/sensitivity/registry.py):
# "falconsai_vit" (Falconsai's ViT NSFW classifier, Apache-2.0, int8 ONNX).
PHOTOS_SENSITIVE_BACKEND = os.getenv("PHOTOS_SENSITIVE_BACKEND", "falconsai_vit")
# Photos larger than this are not checked (videos: PHOTOS_VIDEO_KEY_FRAMES_*).
PHOTOS_SENSITIVE_MAX_FILE_BYTES = int(
    os.getenv("PHOTOS_SENSITIVE_MAX_FILE_BYTES", str(64 * 1024 * 1024))
)

# The map under a photo's coordinates in its properties panel: the tile
# server's URL template ({z}/{x}/{y}, optionally {s} and {r}) and the
# attribution its usage policy requires, shown on the map (HTML allowed, e.g.
# '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>
# contributors'). No map unless both are set: there is no default server, the
# tile server learns the location of every photo whose map is opened.
PHOTOS_MAP_TILE_URL = os.getenv("PHOTOS_MAP_TILE_URL", "").strip()
PHOTOS_MAP_TILE_ATTRIBUTION = os.getenv("PHOTOS_MAP_TILE_ATTRIBUTION", "").strip()
# Optional: the same map in a dark style, drawn under a dark theme (e.g. Esri's
# Dark Gray Canvas next to Light Gray). Shares the attribution above.
PHOTOS_MAP_TILE_URL_DARK = os.getenv("PHOTOS_MAP_TILE_URL_DARK", "").strip()
