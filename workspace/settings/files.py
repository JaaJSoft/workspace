"""Files module: quotas, listing limits, trash retention, WebDAV locks."""

import os

from .cache import _REDIS_WEBDAV_URL
from .env import env_bool

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

# Malware scanning. Off by default: the Raspberry Pi deployment target cannot
# run a ClamAV daemon, and a single-trusted-user instance does not need one.
FILES_MALWARE_SCAN_ENABLED = env_bool("FILES_MALWARE_SCAN_ENABLED", False)
FILES_MALWARE_SCANNER = os.getenv("FILES_MALWARE_SCANNER", "clamav")

# Unix socket takes precedence when set (accepts a bare path or a unix:// URL).
FILES_CLAMAV_SOCKET = os.getenv("FILES_CLAMAV_SOCKET", "")
FILES_CLAMAV_HOST = os.getenv("FILES_CLAMAV_HOST", "127.0.0.1")
FILES_CLAMAV_PORT = int(os.getenv("FILES_CLAMAV_PORT", "3310"))
FILES_CLAMAV_TIMEOUT = float(os.getenv("FILES_CLAMAV_TIMEOUT", "60"))

# Bytes streamed to the daemon at most. Deliberately the same 25 MB as clamd's
# own StreamMaxLength default: the effective ceiling is the lower of the two, so
# a larger value here would be silently overruled by the daemon and only make
# the configuration lie. Raising one means raising the other.
FILES_MALWARE_SCAN_MAX_BYTES = int(
    os.getenv("FILES_MALWARE_SCAN_MAX_BYTES", str(25 * 1024 * 1024))
)

# What a detection does: "block" quarantines, "flag" records and leaves the
# file readable.
FILES_MALWARE_ON_DETECTION = os.getenv("FILES_MALWARE_ON_DETECTION", "block")
# What an unscannable file does: "open" leaves it readable, "closed" blocks it.
FILES_MALWARE_ON_ERROR = os.getenv("FILES_MALWARE_ON_ERROR", "open")

# Public "drop" share links: the only unauthenticated write path in the app.
# These are ceilings, not defaults. A link's own max_file_bytes /
# max_file_count can lower them and never raise them, so no configuration
# leaves an anonymous upload unbounded.
FILES_DROP_MAX_FILE_BYTES = int(
    os.getenv("FILES_DROP_MAX_FILE_BYTES", str(100 * 1024 * 1024))
)  # 100 MiB
FILES_DROP_MAX_FILE_COUNT = int(os.getenv("FILES_DROP_MAX_FILE_COUNT", "100"))
# Window over which uploads coalesce into a single notification to the owner.
FILES_DROP_NOTIFY_WINDOW_SECONDS = int(
    os.getenv("FILES_DROP_NOTIFY_WINDOW_SECONDS", "60")
)
# Throttle rates for the anonymous upload endpoint, per link and per client.
FILES_DROP_UPLOAD_RATE_TOKEN = os.getenv("FILES_DROP_UPLOAD_RATE_TOKEN", "20/min")
FILES_DROP_UPLOAD_RATE_IP = os.getenv("FILES_DROP_UPLOAD_RATE_IP", "60/min")
