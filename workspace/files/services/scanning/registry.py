"""Backend selection for malware scanning.

One backend exists today. The dict is the seam for a second, not an
invitation to add one speculatively.
"""

from __future__ import annotations

import logging

from django.conf import settings

from workspace.common.logging import scrub

from ...models import FileScan
from .base import Scanner, ScannerHealth, ScanVerdict

logger = logging.getLogger(__name__)


def _clamav():
    from .clamav import ClamAVScanner

    return ClamAVScanner()


_BACKENDS = {"clamav": _clamav}


class MisconfiguredScanner(Scanner):
    """Stands in for a FILES_MALWARE_SCANNER value that names no backend.

    Returning None instead would make the scan task report "disabled", so a
    typo in the setting would quietly switch the feature off on an instance
    whose administrator believes it is on - and FILES_MALWARE_ON_ERROR would
    never see a failure to act on, because there would be no verdict at all.

    An ERROR verdict keeps the configured policy in charge (fail-closed blocks
    the file, fail-open lets it through and counts it) and surfaces the
    misconfiguration on the admin dashboard's scanner card.
    """

    def __init__(self, key):
        self._key = key

    def _message(self):
        return f"unknown malware scanner backend {self._key!r}"

    def scan(self, stream, *, name=""):
        return ScanVerdict(status=FileScan.Status.ERROR, detail=self._message())

    def health(self):
        return ScannerHealth(reachable=False, error=self._message())


def get_scanner():
    """The configured Scanner, or None when scanning is off.

    Never returns None while scanning is enabled: a misconfigured backend
    yields a MisconfiguredScanner rather than nothing, so the caller cannot
    mistake "we could not scan" for "we were not asked to".
    """
    if not getattr(settings, "FILES_MALWARE_SCAN_ENABLED", False):
        return None
    key = getattr(settings, "FILES_MALWARE_SCANNER", "clamav")
    factory = _BACKENDS.get(key)
    if factory is None:
        logger.error("Unknown malware scanner backend %s", scrub(key))
        return MisconfiguredScanner(key)
    return factory()
