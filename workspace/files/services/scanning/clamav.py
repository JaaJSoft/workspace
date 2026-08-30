"""ClamAV backend, speaking the clamd INSTREAM protocol.

Uses clamav_client's low-level socket classes rather than its high-level
``get_scanner()`` helper: that helper scans by filename, which would tie this
feature to FileSystemStorage. ``instream`` accepts any file-like object and
reads it in small chunks, so nothing is ever fully buffered.
"""

from __future__ import annotations

import logging

from clamav_client.clamd import (
    BufferTooLongError,
    ClamdError,
    ClamdNetworkSocket,
    ClamdUnixSocket,
)
from django.conf import settings

from workspace.common.logging import scrub

from ...models import FileScan
from .base import Scanner, ScannerHealth, ScanVerdict

logger = logging.getLogger(__name__)

# Health checks run inside an admin request; a dead daemon must not hold the
# page for the full scan timeout.
HEALTH_TIMEOUT = 2.0

_SIGNATURE_MAX = FileScan._meta.get_field("signature").max_length
_DETAIL_MAX = FileScan._meta.get_field("detail").max_length


class ClamAVScanner(Scanner):
    """Scans by streaming to a clamd daemon over a Unix or TCP socket."""

    def _client(self, timeout=None):
        socket_path = getattr(settings, "FILES_CLAMAV_SOCKET", "")
        effective = (
            timeout
            if timeout is not None
            else float(getattr(settings, "FILES_CLAMAV_TIMEOUT", 60))
        )
        if socket_path:
            return ClamdUnixSocket(path=socket_path, timeout=effective)
        return ClamdNetworkSocket(
            host=getattr(settings, "FILES_CLAMAV_HOST", "127.0.0.1"),
            port=int(getattr(settings, "FILES_CLAMAV_PORT", 3310)),
            timeout=effective,
        )

    def scan(self, stream, *, name=""):
        try:
            result = self._client().instream(stream)
        except BufferTooLongError:
            # The daemon's own StreamMaxLength, not ours. Same meaning as our
            # cap: we cannot vouch for these bytes, and saying so is honest.
            return ScanVerdict(
                status=FileScan.Status.SKIPPED,
                detail="daemon stream size limit exceeded",
            )
        except (ClamdError, OSError) as exc:
            logger.warning(
                "Malware scan failed for %s: %s", scrub(name), scrub(str(exc))
            )
            return ScanVerdict(
                status=FileScan.Status.ERROR, detail=str(exc)[:_DETAIL_MAX]
            )

        if not result:
            return ScanVerdict(
                status=FileScan.Status.ERROR, detail="empty response from daemon"
            )

        status, reason = next(iter(result.values()))
        if status == "OK":
            return ScanVerdict(status=FileScan.Status.CLEAN)
        if status == "FOUND":
            return ScanVerdict(
                status=FileScan.Status.INFECTED,
                signature=(reason or "")[:_SIGNATURE_MAX],
            )
        return ScanVerdict(
            status=FileScan.Status.ERROR, detail=(reason or status)[:_DETAIL_MAX]
        )

    def health(self):
        try:
            client = self._client(timeout=HEALTH_TIMEOUT)
            client.ping()
            version = client.version()
        except (ClamdError, OSError) as exc:
            return ScannerHealth(reachable=False, error=str(exc)[:_DETAIL_MAX])
        return ScannerHealth(reachable=True, version=version[:_DETAIL_MAX])
