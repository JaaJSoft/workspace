import logging

from celery import shared_task

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
    msg['To'] = event.organizer_email
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
        server.sendmail(account.email, [event.organizer_email], msg.as_string())
    finally:
        server.quit()


@shared_task(name='calendar.sync_external_calendar', ignore_result=True, soft_time_limit=120)
def sync_external_calendar_task(external_calendar_uuid):
    """Sync a single external ICS calendar feed."""
    from workspace.calendar.models_external import ExternalCalendar
    from workspace.calendar.services.ics_sync import sync_external_calendar

    try:
        ext = ExternalCalendar.objects.select_related('calendar').get(
            uuid=external_calendar_uuid,
        )
    except ExternalCalendar.DoesNotExist:
        return

    try:
        sync_external_calendar(ext)
    except Exception as exc:
        ext.last_error = str(exc)
        ext.save(update_fields=['last_error'])
        raise


@shared_task(name='calendar.sync_all_external_calendars', ignore_result=True)
def sync_all_external_calendars():
    """Dispatch sync tasks for all active external calendars."""
    from workspace.calendar.models_external import ExternalCalendar

    for ext_uuid in ExternalCalendar.objects.filter(
        is_active=True,
    ).values_list('uuid', flat=True):
        sync_external_calendar_task.delay(str(ext_uuid))
