import logging
from datetime import timedelta

from celery import shared_task

from workspace.common.celery_claim import cas_finalize, dispatch_due
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@shared_task(name="calendar.send_ics_reply", ignore_result=True, soft_time_limit=30)
def send_ics_reply(event_id, user_id, response_status):
    """Send an iCalendar REPLY email to the event organizer."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formatdate, make_msgid

    from django.contrib.auth import get_user_model

    from workspace.calendar.models import Event
    from workspace.calendar.services.ics_builder import build_reply
    from workspace.mail.services.smtp import connect_smtp

    User = get_user_model()

    try:
        event = Event.objects.select_related("calendar__mail_account").get(pk=event_id)
    except Event.DoesNotExist:
        return

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    account = event.calendar.mail_account
    if not account:
        return

    ics_data = build_reply(event, user, response_status)
    status_label = "Accepted" if response_status == "accepted" else "Declined"

    msg = MIMEMultipart("mixed")
    msg["From"] = f"{user.get_full_name() or user.username} <{account.email}>"
    msg["To"] = event.external_organizer
    msg["Subject"] = f"{status_label}: {event.title}"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=account.email.split("@")[-1])

    body = MIMEText(
        f'{user.get_full_name() or user.username} has {response_status} "{event.title}".',
        "plain",
        "utf-8",
    )
    msg.attach(body)

    cal_part = MIMEText(ics_data.decode("utf-8"), "calendar", "utf-8")
    cal_part.set_param("method", "REPLY")
    msg.attach(cal_part)

    server = connect_smtp(account)
    try:
        server.sendmail(account.email, [event.external_organizer], msg.as_string())
    finally:
        server.quit()


@shared_task(
    name="calendar.sync_external_calendar", ignore_result=True, soft_time_limit=120
)
def sync_external_calendar_task(external_calendar_uuid, claim_token=None):
    """Sync a single external ICS calendar feed.

    ``claim_token`` is the value the dispatcher CAS-wrote into
    ``last_synced_at``. The worker finalises its claim by CAS-pinning
    that exact value (see :func:`workspace.common.celery_claim.cas_finalize`)
    so a duplicate Celery delivery whose row was already finalised by
    the winning worker matches zero rows and bails before re-fetching
    the feed. Calls without a token (manual ``sync`` button, direct
    test calls) skip the CAS.
    """
    from django.utils import timezone

    from workspace.calendar.models_external import ExternalCalendar
    from workspace.calendar.services.ics_sync import sync_external_calendar

    try:
        ext = ExternalCalendar.objects.select_related("calendar").get(
            uuid=external_calendar_uuid,
        )
    except ExternalCalendar.DoesNotExist:
        return

    if claim_token and not cas_finalize(
        ExternalCalendar,
        ext.pk,
        claim_field="last_synced_at",
        claim_token=claim_token,
        updates={"last_synced_at": timezone.now()},
        extra_where={"is_active": True},
    ):
        logger.info(
            "External calendar sync skipped (claimed by another worker): ext=%s",
            scrub(str(ext.pk)),
        )
        return
    if claim_token:
        ext.refresh_from_db(fields=["last_synced_at"])

    try:
        sync_external_calendar(ext)
    except Exception as exc:
        ext.last_error = str(exc)
        ext.save(update_fields=["last_error"])
        raise


@shared_task(name="calendar.sync_all_external_calendars", ignore_result=True)
def sync_all_external_calendars():
    """Dispatch sync tasks for active external calendars due for sync.

    The claim-and-enqueue loop lives in
    :func:`workspace.common.celery_claim.dispatch_due`; what stays here is
    the definition of "due", which is the only part specific to this model.

    Filters on ``last_synced_at`` so the ``(is_active, last_synced_at)``
    composite index is used end-to-end. The 900s threshold matches the
    default ``sync_interval`` and the typical celery-beat cadence;
    ``last_synced_at IS NULL`` covers calendars never synced before
    (note that the dispatcher's claim — a future timestamp — also flips
    such rows out of the IS NULL state, which is what we want).
    """
    from django.db.models import Q
    from django.utils import timezone

    from workspace.calendar.models_external import ExternalCalendar

    threshold = timezone.now() - timedelta(seconds=900)
    due = ExternalCalendar.objects.filter(
        Q(last_synced_at__lt=threshold) | Q(last_synced_at__isnull=True),
        is_active=True,
    ).only("pk", "last_synced_at")
    return dispatch_due(
        due,
        sync_external_calendar_task,
        claim_field="last_synced_at",
        extra_where={"is_active": True},
        label="external calendar sync",
        log=logger,
    ).as_dict()


@shared_task(name="calendar.notify_today_events", ignore_result=True)
def notify_today_events():
    """Notify each user of their remaining events today.

    Runs every morning. Keyed per event through ``notify_stream`` (a
    recurring occurrence keys on its master row), so a rerun merges into the
    existing unread notification instead of stacking a duplicate. Priority
    "low": the badge and the bell are the point - a push per calendar entry
    every morning would be noise. Deleting an event CASCADEs its rows away;
    displaying it in the calendar or the event detail marks them read.
    """
    from datetime import datetime, time

    from django.contrib.auth import get_user_model
    from django.utils import timezone as dj_timezone

    from workspace.calendar.upcoming import VirtualOccurrence, get_upcoming_for_user
    from workspace.notifications.services.notifications import notify_stream

    now = dj_timezone.now()
    end_of_today = dj_timezone.make_aware(
        datetime.combine(dj_timezone.localdate(), time.max),
        dj_timezone.get_current_timezone(),
    )

    notified = 0
    for user in get_user_model().objects.filter(is_active=True).only("pk"):
        for event in get_upcoming_for_user(user, now, end_of_today):
            source = event.master if isinstance(event, VirtualOccurrence) else event
            if event.all_day:
                body = "All day today"
            else:
                body = f"Today at {dj_timezone.localtime(event.start):%H:%M}"
            url = ""
            if not isinstance(event, VirtualOccurrence):
                url = f"/calendar?event={event.uuid}"
            notify_stream(
                recipient_ids=[user.pk],
                source=source,
                origin="calendar",
                title=event.title,
                body=body,
                url=url,
                default_priority="low",
            )
            notified += 1
    if notified:
        logger.info("Today's-event notifications refreshed for %d events", notified)
    return notified
