"""Callbacks wired into the UNFOLD settings dict (see workspace/settings/admin.py).

Everything here runs inside an admin request: the environment label, the
sidebar badge counts, and the system-health cards on the admin index. Badge
callables must stay single COUNT queries - the sidebar renders on every admin
page.
"""

from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone


def environment_callback(request):
    if settings.DEBUG:
        return ["Development", "info"]
    return ["Production", "danger"]


def _last_24h():
    return timezone.now() - timedelta(hours=24)


def mail_sync_error_count(request):
    from workspace.mail.services.imap_sync import accounts_with_sync_errors

    return accounts_with_sync_errors().count()


def external_calendar_error_count(request):
    from workspace.calendar.services.ics_sync import external_calendars_with_errors

    return external_calendars_with_errors().count()


def failed_ai_task_count(request):
    from workspace.ai.services.ai_task import failed_task_count

    return failed_task_count(_last_24h())


def thumbnail_failure_count(request):
    from workspace.files.services.thumbnails.failures import parked_count

    return parked_count()


def quarantined_file_count(request):
    from workspace.files.services.scanning.policy import blocked_scans_qs

    return blocked_scans_qs().count()


def scanner_error_count(request):
    from workspace.files.models import FileScan
    from workspace.files.services.scanning.policy import scan_enabled

    if not scan_enabled():
        return 0
    return FileScan.objects.filter(
        status=FileScan.Status.ERROR, scanned_at__gte=_last_24h()
    ).count()


def failed_import_job_count(request):
    from workspace.imports.services.jobs import failed_job_count

    return failed_job_count(_last_24h())


# Sidebar badge wrappers: unfold renders any non-empty badge value - a count
# of 0 would show as a red "0" pill - while None hides the badge entirely.


def mail_sync_error_badge(request):
    return mail_sync_error_count(request) or None


def external_calendar_error_badge(request):
    return external_calendar_error_count(request) or None


def failed_ai_task_badge(request):
    return failed_ai_task_count(request) or None


def thumbnail_failure_badge(request):
    return thumbnail_failure_count(request) or None


def quarantined_file_badge(request):
    return quarantined_file_count(request) or None


def failed_import_job_badge(request):
    return failed_import_job_count(request) or None


# Probing the daemon is a network call, so it runs only on the admin index -
# never from a badge, which renders on every admin page - and its result is
# cached. A dead daemon therefore costs one short-timeout probe a minute
# rather than a stall on every page load.
_SCANNER_HEALTH_CACHE_KEY = "files:scanner:health"
_SCANNER_HEALTH_TTL = 60


def scanner_health_card(request):
    """A health card for the malware scanner, or None when scanning is off."""
    from django.core.cache import cache

    from workspace.files.services.scanning.policy import scan_enabled
    from workspace.files.services.scanning.registry import get_scanner

    if not scan_enabled():
        return None

    cached = cache.get(_SCANNER_HEALTH_CACHE_KEY)
    if cached is None:
        scanner = get_scanner()
        if scanner is None:
            cached = {"reachable": False, "label": "not configured"}
        else:
            health = scanner.health()
            cached = {
                "reachable": health.reachable,
                "label": health.version or health.error or "unreachable",
            }
        cache.set(_SCANNER_HEALTH_CACHE_KEY, cached, _SCANNER_HEALTH_TTL)

    return {
        "title": "Malware scanner",
        "icon": "shield",
        "description": "antivirus daemon reachability",
        "value": cached["label"],
        "url": reverse("admin:files_filescan_changelist"),
        "tone": "success" if cached["reachable"] else "danger",
    }


def dashboard_callback(request, context):
    from workspace.files.services.scanning.policy import blocked_statuses

    cards = [
        {
            "title": "Mail sync errors",
            "icon": "alternate_email",
            "description": "active accounts whose last sync failed",
            "value": mail_sync_error_count(request),
            "url": reverse("admin:mail_mailaccount_changelist")
            + "?sync=error&is_active__exact=1",
        },
        {
            "title": "Calendar sync errors",
            "icon": "cloud_sync",
            "description": "external calendars whose last sync failed",
            "value": external_calendar_error_count(request),
            "url": reverse("admin:calendar_externalcalendar_changelist")
            + "?sync=error&is_active__exact=1",
        },
        {
            "title": "Failed AI tasks",
            "icon": "neurology",
            "description": "failed in the last 24 hours",
            "value": failed_ai_task_count(request),
            "url": reverse("admin:ai_aitask_changelist") + "?status__exact=failed",
        },
        {
            "title": "Parked thumbnails",
            "icon": "broken_image",
            "description": "files whose thumbnail generation failed",
            "value": thumbnail_failure_count(request),
            "url": reverse("admin:files_thumbnailfailure_changelist"),
        },
        {
            "title": "Quarantined files",
            "icon": "shield_lock",
            "description": "files the malware policy currently blocks",
            "value": quarantined_file_count(request),
            # The changelist has to list exactly what the count counted: under
            # FILES_MALWARE_ON_ERROR=closed a hard-coded status__exact=infected
            # hides the error rows the number includes.
            "url": reverse("admin:files_filescan_changelist")
            + f"?status__in={','.join(sorted(blocked_statuses()))}",
        },
        {
            "title": "Scanner errors",
            "icon": "gpp_maybe",
            "description": "files that could not be scanned in the last 24 hours",
            "value": scanner_error_count(request),
            "url": reverse("admin:files_filescan_changelist") + "?status__exact=error",
        },
        {
            "title": "Failed imports",
            "icon": "cloud_download",
            "description": "jobs failed in the last 24 hours",
            "value": failed_import_job_count(request),
            "url": reverse("admin:imports_importjob_changelist")
            + "?status__exact=failed",
        },
    ]
    for card in cards:
        card["tone"] = "danger" if card["value"] else "success"
    health = scanner_health_card(request)
    if health is not None:
        cards.append(health)
    context["health_cards"] = cards
    return context
