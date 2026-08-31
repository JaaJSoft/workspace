"""Deployment-time validation of the files module's configuration."""

from django.conf import settings
from django.core.checks import Error, register


@register()
def check_malware_scanner_backend(app_configs, **kwargs):
    """Refuse to start when scanning is on but the backend name is unknown.

    The runtime already degrades safely - get_scanner() hands back a scanner
    that reports ERROR, so FILES_MALWARE_ON_ERROR decides what happens. But
    degrading safely is not the same as being configured, and the symptom of a
    typo (every upload recorded as unscannable) is easy to mistake for the
    daemon being down. Failing here says which of the two it is, at deploy
    time, before any file has been accepted.
    """
    from workspace.files.services.scanning.registry import _BACKENDS

    if not getattr(settings, "FILES_MALWARE_SCAN_ENABLED", False):
        return []

    key = getattr(settings, "FILES_MALWARE_SCANNER", "clamav")
    if key in _BACKENDS:
        return []

    known = ", ".join(sorted(_BACKENDS))
    return [
        Error(
            f"FILES_MALWARE_SCANNER is set to {key!r}, which is not a known "
            "malware scanner backend.",
            hint=f"Set it to one of: {known}. Or unset "
            "FILES_MALWARE_SCAN_ENABLED to turn scanning off.",
            id="files.E001",
        )
    ]
