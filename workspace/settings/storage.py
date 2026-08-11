"""Static files, media files and storage backends."""

import os
from pathlib import Path

from .base import BASE_DIR, DEBUG

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

# Storage backends (Django 5 style)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
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
