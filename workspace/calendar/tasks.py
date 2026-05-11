import logging
from datetime import timedelta

from celery import shared_task

from workspace.common.celery_claim import (
    DISPATCH_LOCK_HORIZON,
    cas_claim,
    cas_finalize,
    cas_rollback,
)
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@shared_task(name='calendar.send_ics_reply', ignore_result=True, soft_time_limit=30)
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
        event = Event.objects.select_related('calendar__mail_account').get(pk=event_id)
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
    status_label = 'Accepted' if response_status == 'accepted' else 'Declined'

    msg = MIMEMultipart('mixed')
    msg['From'] = f'{user.get_full_name() or user.username} <{account.email}>'
    msg['To'] = event.external_organizer
    msg['Subject'] = f'{status_label}: {event.title}'
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=account.email.split('@')[-1])

    body = MIMEText(
        f'{user.get_full_name() or user.username} has {response_status} "{event.title}".',
        'plain', 'utf-8',
    )
    msg.attach(body)

    cal_part = MIMEText(ics_data.decode('utf-8'), 'calendar', 'utf-8')
    cal_part.set_param('method', 'REPLY')
    msg.attach(cal_part)

    server = connect_smtp(account)
    try:
        server.sendmail(account.email, [event.external_organizer], msg.as_string())
    finally:
        server.quit()


@shared_task(name='calendar.sync_external_calendar', ignore_result=True, soft_time_limit=120)
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
        ext = ExternalCalendar.objects.select_related('calendar').get(
            uuid=external_calendar_uuid,
        )
    except ExternalCalendar.DoesNotExist:
        return

    if claim_token and not cas_finalize(
        ExternalCalendar, ext.pk,
        claim_field='last_synced_at', claim_token=claim_token,
        updates={'last_synced_at': timezone.now()},
        extra_where={'is_active': True},
    ):
        logger.info(
            'External calendar sync skipped (claimed by another worker): ext=%s',
            scrub(str(ext.pk)),
        )
        return
    if claim_token:
        ext.refresh_from_db(fields=['last_synced_at'])

    try:
        sync_external_calendar(ext)
    except Exception as exc:
        ext.last_error = str(exc)
        ext.save(update_fields=['last_error'])
        raise


@shared_task(name='calendar.sync_all_external_calendars', ignore_result=True)
def sync_all_external_calendars():
    """Dispatch sync tasks for active external calendars due for sync.

    Each row is CAS-claimed by advancing ``last_synced_at`` past the due
    threshold (see :mod:`workspace.common.celery_claim`). Only the
    dispatcher whose UPDATE affected a row enqueues the worker;
    concurrent dispatcher runs race on the same predicate and the
    database guarantees exactly one winner per row.

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
    due = (
        ExternalCalendar.objects
        .filter(
            Q(last_synced_at__lt=threshold) | Q(last_synced_at__isnull=True),
            is_active=True,
        )
        .only('pk', 'uuid', 'last_synced_at')
    )
    for ext in due:
        original = ext.last_synced_at
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=original,
            extra_where={'is_active': True},
        )
        if token is None:
            continue
        try:
            sync_external_calendar_task.delay(str(ext.uuid), token.isoformat())
        except Exception:
            # Broker errors etc. - roll back the claim so the row stays
            # due and re-fires on the next dispatcher pass instead of
            # being parked at the token for DISPATCH_LOCK_HORIZON. Keep
            # looping so other due rows still get a chance.
            cas_rollback(ExternalCalendar, ext.pk, 'last_synced_at', original)
            logger.exception(
                'Failed to enqueue external calendar sync: ext=%s',
                scrub(str(ext.pk)),
            )
            continue


# Re-exported for backwards-compat with any caller that imported it from
# this module before the helper was extracted.
__all__ = [
    'send_ics_reply',
    'sync_external_calendar_task',
    'sync_all_external_calendars',
    'DISPATCH_LOCK_HORIZON',
]
