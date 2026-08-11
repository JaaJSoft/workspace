"""Files module: quotas, listing limits, trash retention, WebDAV locks."""

import os

from .cache import _REDIS_WEBDAV_URL

# Recent files listing limits
RECENT_FILES_LIMIT = int(os.getenv("RECENT_FILES_LIMIT", "25"))
RECENT_FILES_MAX_LIMIT = int(os.getenv("RECENT_FILES_MAX_LIMIT", "200"))
TRASH_RETENTION_DAYS = int(os.getenv("TRASH_RETENTION_DAYS", "30"))

STORAGE_QUOTA_BYTES = int(
    os.getenv("STORAGE_QUOTA_BYTES", str(1 * 1024 * 1024 * 1024))
)  # 1 GB

# Max total uncompressed bytes allowed when extracting an archive (zip bomb defence).
FILES_EXTRACT_MAX_BYTES = int(
    os.getenv("FILES_EXTRACT_MAX_BYTES", str(2 * 1024 * 1024 * 1024))
)  # 2 GiB
# Max number of entries allowed when extracting an archive.
FILES_EXTRACT_MAX_ENTRIES = int(os.getenv("FILES_EXTRACT_MAX_ENTRIES", "10000"))

# WebDAV lock storage
# Use dedicated Redis DB when available so locks are shared across gunicorn
# workers; otherwise fall back to in-process storage (dev / single worker).
WEBDAV_LOCK_STORAGE_URL = _REDIS_WEBDAV_URL
