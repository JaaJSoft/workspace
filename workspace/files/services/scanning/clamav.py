"""ClamAV backend, speaking the clamd INSTREAM protocol.

Uses ``instream``, which accepts any file-like object and reads it in small
chunks, so nothing is ever fully buffered and the feature is not tied to
FileSystemStorage.
"""

from __future__ import annotations

import logging

from clamdpy.exceptions import BufferTooLongError, ClamdException
from clamdpy.sockets import ClamdNetworkSocket, ClamdUnixSocket
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


def _describe(exc):
    """The message of a clamd failure, as text.

    clamdpy's errors keep their message in an attribute and pass their
    constructor arguments to ``BaseException`` untouched, so ``str()`` of a
    refused connection reads ``('Error while connecting to ...', 111)``.
    """
    for attribute in ("msg", "error"):
        text = getattr(exc, attribute, None)
        if isinstance(text, str):
            return text
    return str(exc)


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
        client = self._client()
        try:
            result = client.instream(stream)
        except BufferTooLongError:
            # The daemon's own StreamMaxLength, not ours. Same meaning as our
            # cap: we cannot vouch for these bytes, and saying so is honest.
            return ScanVerdict(
                status=FileScan.Status.SKIPPED,
                detail="daemon stream size limit exceeded",
            )
        except (ClamdException, OSError) as exc:
            # clamdpy's errors are not OSErrors: it wraps a refused connection
            # and a reply it cannot parse, an empty one included.
            logger.warning(
                "Malware scan failed for %s: %s", scrub(name), scrub(_describe(exc))
            )
            return ScanVerdict(
                status=FileScan.Status.ERROR, detail=_describe(exc)[:_DETAIL_MAX]
            )

        if result.status == "OK":
            return ScanVerdict(status=FileScan.Status.CLEAN)
        if result.status == "FOUND":
            return ScanVerdict(
                status=FileScan.Status.INFECTED,
                signature=(result.reason or "")[:_SIGNATURE_MAX],
            )
        return ScanVerdict(
            status=FileScan.Status.ERROR,
            detail=(result.reason or result.status)[:_DETAIL_MAX],
        )

    def health(self):
        client = self._client(timeout=HEALTH_TIMEOUT)
        try:
            client.ping()
            version = client.version(raw=True)
        except (ClamdException, OSError) as exc:
            return ScannerHealth(reachable=False, error=_describe(exc)[:_DETAIL_MAX])
        return ScannerHealth(reachable=True, version=version[:_DETAIL_MAX])
