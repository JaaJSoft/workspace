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


def failed_import_job_badge(request):
    return failed_import_job_count(request) or None


def dashboard_callback(request, context):
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
    context["health_cards"] = cards
    return context
