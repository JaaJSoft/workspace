import base64
import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from workspace.core.sse_registry import notify_sse_inline

from .call_models import Call, CallParticipant
from .models import ConversationMember, Message

logger = logging.getLogger(__name__)
User = get_user_model()


class CallError(Exception):
    pass


def _get_system_user():
    return User.objects.get(username='system')


def _active_member_ids(conversation, exclude_user=None):
    qs = ConversationMember.objects.filter(
        conversation=conversation, left_at__isnull=True,
    ).values_list('user_id', flat=True)
    if exclude_user:
        qs = qs.exclude(user_id=exclude_user.id)
    return list(qs)


def _active_participant_ids(call, exclude_user=None):
    qs = CallParticipant.objects.filter(
        call=call, left_at__isnull=True,
    ).values_list('user_id', flat=True)
    if exclude_user:
        qs = qs.exclude(user_id=exclude_user.id)
    return list(qs)


def _send_call_event(event, data, user_ids):
    for uid in user_ids:
        notify_sse_inline(event=f'chat.{event}', data=data, user_id=uid)


def send_push_notification(call, exclude_user=None):
    from workspace.notifications.services import notify_many
    member_ids = _active_member_ids(call.conversation, exclude_user=exclude_user)
    recipients = User.objects.filter(id__in=member_ids)
    if recipients:
        notify_many(
            recipients=recipients,
            origin='chat',
            title=f'{call.initiator.username} vous appelle',
            body=f'Appel vocal dans {call.conversation.title or "conversation"}',
            url=f'/chat?call={call.uuid}',
            priority='high',
        )


def start_call(*, conversation, initiator):
    call = Call.objects.create(conversation=conversation, initiator=initiator)
    CallParticipant.objects.create(call=call, user=initiator)

    member_ids = _active_member_ids(conversation, exclude_user=initiator)
    _send_call_event('call.incoming', {
        'call_id': str(call.uuid),
        'conversation_id': str(conversation.uuid),
        'initiator_id': initiator.id,
        'initiator_name': initiator.username,
    }, member_ids)

    send_push_notification(call, exclude_user=initiator)

    from django.conf import settings as django_settings
    from .call_tasks import call_timeout
    if getattr(django_settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        # In eager mode, countdown is ignored — use a background thread instead
        import threading
        threading.Timer(30, call_timeout, args=[str(call.uuid)]).start()
    else:
        call_timeout.apply_async(args=[str(call.uuid)], countdown=30)

    return call


def join_call(*, call, user):
    if call.status not in ('ringing', 'active'):
        raise CallError('Call is no longer available')

    already_in = CallParticipant.objects.filter(
        user=user, left_at__isnull=True,
    ).exclude(call=call).exists()
    if already_in:
        raise CallError('Already in another call')

    CallParticipant.objects.create(call=call, user=user)

    active_count = CallParticipant.objects.filter(call=call, left_at__isnull=True).count()
    if active_count >= 2 and call.status == 'ringing':
        call.status = 'active'
        call.started_at = timezone.now()
        call.save(update_fields=['status', 'started_at'])

    participant_ids = _active_participant_ids(call, exclude_user=user)
    _send_call_event('call.participant_joined', {
        'call_id': str(call.uuid),
        'user_id': user.id,
        'user_name': user.username,
    }, participant_ids)

    return call


def leave_call(*, call, user):
    participant = CallParticipant.objects.filter(
        call=call, user=user, left_at__isnull=True,
    ).first()
    if not participant:
        return

    participant.left_at = timezone.now()
    participant.save(update_fields=['left_at'])

    remaining = CallParticipant.objects.filter(call=call, left_at__isnull=True).count()

    participant_ids = _active_participant_ids(call)
    _send_call_event('call.participant_left', {
        'call_id': str(call.uuid),
        'user_id': user.id,
    }, participant_ids)

    if remaining == 0:
        _end_call(call)


def reject_call(*, call, user):
    _send_call_event('call.rejected', {
        'call_id': str(call.uuid),
        'user_id': user.id,
    }, [call.initiator_id])


def relay_signal(*, call, from_user, to_user_id, signal_type, payload):
    active_users = set(
        CallParticipant.objects.filter(
            call=call, left_at__isnull=True,
        ).values_list('user_id', flat=True)
    )
    if from_user.id not in active_users or to_user_id not in active_users:
        raise CallError('Both users must be active call participants')

    notify_sse_inline(
        event='chat.call.signal',
        data={
            'call_id': str(call.uuid),
            'from_user': from_user.id,
            'type': signal_type,
            'payload': payload,
        },
        user_id=to_user_id,
    )


def update_mute(*, call, user, muted):
    CallParticipant.objects.filter(
        call=call, user=user, left_at__isnull=True,
    ).update(muted=muted)

    participant_ids = _active_participant_ids(call, exclude_user=user)
    _send_call_event('call.mute_changed', {
        'call_id': str(call.uuid),
        'user_id': user.id,
        'muted': muted,
    }, participant_ids)


def _end_call(call):
    call.status = 'ended'
    call.ended_at = timezone.now()
    call.save(update_fields=['status', 'ended_at'])

    duration = ''
    if call.started_at:
        delta = call.ended_at - call.started_at
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        duration = f'{minutes}:{seconds:02d}'

    participant_count = CallParticipant.objects.filter(call=call).count()

    system_user = _get_system_user()
    body = f'Appel vocal \u00b7 {participant_count} participants \u00b7 {duration}' if duration else f'Appel vocal \u00b7 {participant_count} participants'
    Message.objects.create(
        conversation=call.conversation,
        author=system_user,
        body=body,
        body_html=f'<p>{body}</p>',
    )

    member_ids = _active_member_ids(call.conversation)
    _send_call_event('call.ended', {
        'call_id': str(call.uuid),
        'duration': duration,
        'participant_count': participant_count,
    }, member_ids)


def generate_ice_servers():
    stun_url = getattr(settings, 'STUN_SERVER_URL', 'stun:stun.l.google.com:19302')
    turn_url = getattr(settings, 'TURN_SERVER_URL', '')
    turn_secret = getattr(settings, 'TURN_SECRET', '')

    servers = [{'urls': stun_url}]

    if turn_url and turn_secret:
        expiry = int(time.time()) + 86400
        username = f'{expiry}:workspace'
        credential = base64.b64encode(
            hmac.new(turn_secret.encode(), username.encode(), hashlib.sha1).digest(),
        ).decode()
        servers.append({
            'urls': turn_url,
            'username': username,
            'credential': credential,
        })

    return servers
