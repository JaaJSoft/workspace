"""Static files, media files and storage backends."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .base import BASE_DIR, DEBUG
from .env import env_bool

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "/static/"

# Define STATIC_ROOT to avoid ImproperlyConfigured errors when using the
# staticfiles app or running collectstatic. Allow override via env var
# STATIC_ROOT; if a relative path is provided, resolve it from BASE_DIR.
_STATIC_ROOT_ENV = os.getenv("STATIC_ROOT")
if _STATIC_ROOT_ENV:
    _static_root = Path(_STATIC_ROOT_ENV)
    if not _static_root.is_absolute():
        _static_root = (BASE_DIR / _static_root).resolve()
else:
    _static_root = BASE_DIR / "staticfiles"

STATIC_ROOT = _static_root

# Media files (user uploads)
MEDIA_ROOT = os.getenv("MEDIA_ROOT", BASE_DIR)
MEDIA_URL = "/media/"

# Restrict uploaded-file permissions to the owning process (owner-only).
# Uploads are served back through Django's FileResponse, never read directly
# by another user (no nginx X-Accel-Redirect, no separate webserver UID), so
# world/group access is unnecessary and triggers CodeQL CWE-732.
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700

# Where blobs live: "local" (MEDIA_ROOT, the default) or "s3" (any
# S3-compatible object storage, configured by the S3_* variables below). The
# key layout is the same on both: a bucket mirrors what MEDIA_ROOT would hold.
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower() or "local"

# Every backend this instance can reach, by name: the one in use, and the
# other one while blobs are copied between them (core's copy_blobs).
BLOB_BACKENDS = {"local": {"backend": "local"}}

_S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
if _S3_BUCKET:
    BLOB_BACKENDS["s3"] = {
        "backend": "s3",
        "bucket": _S3_BUCKET,
        # Keys go under this prefix, so one bucket can hold several instances.
        "prefix": os.getenv("S3_PREFIX", ""),
        # Unset for AWS; the server's URL for any other S3-compatible store.
        "endpoint_url": os.getenv("S3_ENDPOINT_URL") or None,
        "region": os.getenv("S3_REGION") or None,
        # Unset to use boto3's own chain (AWS_* variables, instance role, ...).
        "access_key_id": os.getenv("S3_ACCESS_KEY_ID") or None,
        "secret_access_key": os.getenv("S3_SECRET_ACCESS_KEY") or None,
        # "path" for most self-hosted servers (MinIO, Garage, ...).
        "addressing_style": os.getenv("S3_ADDRESSING_STYLE") or None,
        # The URL clients reach the store at, when it differs from
        # S3_ENDPOINT_URL (an internal service name behind a public proxy).
        "presign_endpoint_url": os.getenv("S3_PRESIGN_ENDPOINT_URL") or None,
        # If-None-Match on writes that must not overwrite. Turn off only for a
        # server that rejects it; saves then rely on a check before the write.
        "conditional_writes": env_bool("S3_CONDITIONAL_WRITES", True),
    }

if STORAGE_BACKEND not in ("local", "s3"):
    raise ImproperlyConfigured(
        f"STORAGE_BACKEND must be 'local' or 's3', got {STORAGE_BACKEND!r}"
    )
if STORAGE_BACKEND not in BLOB_BACKENDS:
    raise ImproperlyConfigured("STORAGE_BACKEND=s3 needs S3_BUCKET")
_BLOB_OPTIONS = BLOB_BACKENDS[STORAGE_BACKEND]

# Blob storage (workspace/common/storage). Two aliases over the same backend.
# "files" holds File.content, whose key is the node's tree path, name as given:
# a new version goes over the old one, and Django's rewrite of the name would
# send "a b.txt" and "a_b.txt" to the same key. "default" keeps a taken name's
# blob and picks another name: mail attachments are keyed by their file name,
# and two "invoice.pdf" in one account are two blobs.
_BLOBS = {"BACKEND": "workspace.common.storage.facade.BlobStorage"}

STORAGES = {
    "default": {**_BLOBS, "OPTIONS": _BLOB_OPTIONS},
    "files": {
        **_BLOBS,
        "OPTIONS": {**_BLOB_OPTIONS, "allow_overwrite": True, "verbatim_names": True},
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Use WhiteNoise's optimized staticfiles storage in non-debug (e.g., containers)
if not DEBUG:
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }
