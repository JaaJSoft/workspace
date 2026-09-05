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
from django.db.models import F, Q

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


def override_applies(scan, file_obj):
    """True when an administrator vouched for the bytes *file_obj* holds now.

    The hash comparison is the whole safety property: the override describes
    one specific content, so replacing it re-blocks the file immediately,
    without waiting for the new bytes to be scanned. A blank hash on either
    side reads as "cannot vouch for these bytes" rather than as a match, which
    two empty strings would otherwise compare as.
    """
    return bool(
        scan.overridden_at
        and scan.content_hash
        and scan.content_hash == file_obj.content_hash
    )


def blocked_scans_qs():
    """The FileScan rows that currently deny access to their file.

    The one definition of "blocked", so the policy, the file querysets and the
    admin dashboard cannot drift apart on what an override does. Empty when
    nothing is blocked at all.
    """
    blocked = blocked_statuses()
    if not blocked:
        return FileScan.objects.none()
    return FileScan.objects.filter(status__in=blocked).exclude(
        Q(overridden_at__isnull=False)
        & ~Q(content_hash="")
        & Q(content_hash=F("file__content_hash"))
    )


def exclude_blocked(queryset):
    """Drop blocked files from a File queryset.

    The subquery selects ``file_id`` from a non-nullable FK on purpose: one
    NULL inside a NOT IN makes the predicate UNKNOWN for every row, which here
    would silently empty every result page.
    """
    if not blocked_statuses():
        return queryset
    return queryset.exclude(pk__in=blocked_scans_qs().values("file_id"))


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
        scan = file_obj.scan
    except FileScan.DoesNotExist:
        return False
    if scan.status not in blocked:
        return False
    return not override_applies(scan, file_obj)


def blocked_reason(file_obj):
    """A short human-readable reason, or None when access is allowed."""
    if not is_blocked(file_obj):
        return None
    scan = file_obj.scan
    if scan.status == FileScan.Status.INFECTED:
        return scan.signature or "Malware detected"
    return "File could not be scanned"
