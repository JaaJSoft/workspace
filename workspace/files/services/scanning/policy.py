"""Turns a stored scan verdict into an access decision.

The decision is derived, never stored: an administrator can flip
FILES_MALWARE_ON_DETECTION or FILES_MALWARE_ON_ERROR without a data
migration, and "flag" mode stays coherent - an infected file is annotated, not
disappeared, so it must keep showing up in search too.

Every entry point short-circuits when nothing is blocked, so a disabled
instance issues no additional query anywhere.
"""

from __future__ import annotations

from django.conf import settings

from ...models import FileScan


def scan_enabled():
    """True when the malware scanning feature is switched on."""
    return bool(getattr(settings, "FILES_MALWARE_SCAN_ENABLED", False))


def blocked_statuses():
    """The FileScan statuses that currently deny access."""
    if not scan_enabled():
        return frozenset()
    statuses = set()
    if getattr(settings, "FILES_MALWARE_ON_DETECTION", "block") == "block":
        statuses.add(FileScan.Status.INFECTED)
    if getattr(settings, "FILES_MALWARE_ON_ERROR", "open") == "closed":
        statuses.add(FileScan.Status.ERROR)
    return frozenset(statuses)


def exclude_blocked(queryset):
    """Drop blocked files from a File queryset.

    The subquery selects ``file_id`` from a non-nullable FK on purpose: one
    NULL inside a NOT IN makes the predicate UNKNOWN for every row, which here
    would silently empty every result page.
    """
    blocked = blocked_statuses()
    if not blocked:
        return queryset
    return queryset.exclude(
        pk__in=FileScan.objects.filter(status__in=blocked).values("file_id")
    )


def with_scan(queryset):
    """Join the scan row, but only when scanning is enabled.

    Keeps a disabled instance emitting exactly the SQL it emitted before the
    feature existed.
    """
    if not scan_enabled():
        return queryset
    return queryset.select_related("scan")


def is_blocked(file_obj):
    """True when the policy denies access to *file_obj*."""
    blocked = blocked_statuses()
    if not blocked:
        return False
    try:
        return file_obj.scan.status in blocked
    except FileScan.DoesNotExist:
        return False


def blocked_reason(file_obj):
    """A short human-readable reason, or None when access is allowed."""
    if not is_blocked(file_obj):
        return None
    scan = file_obj.scan
    if scan.status == FileScan.Status.INFECTED:
        return scan.signature or "Malware detected"
    return "File could not be scanned"
