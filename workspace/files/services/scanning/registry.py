"""Backend selection for malware scanning.

One backend exists today. The dict is the seam for a second, not an
invitation to add one speculatively.
"""

from __future__ import annotations

import logging

from django.conf import settings

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def _clamav():
    from .clamav import ClamAVScanner

    return ClamAVScanner()


_BACKENDS = {"clamav": _clamav}


def get_scanner():
    """The configured Scanner, or None when scanning is off or misconfigured."""
    if not getattr(settings, "FILES_MALWARE_SCAN_ENABLED", False):
        return None
    key = getattr(settings, "FILES_MALWARE_SCANNER", "clamav")
    factory = _BACKENDS.get(key)
    if factory is None:
        logger.error("Unknown malware scanner backend %s", scrub(key))
        return None
    return factory()
