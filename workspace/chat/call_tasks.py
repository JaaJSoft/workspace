import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from workspace.core.sse_registry import notify_sse_inline

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(name='chat.call_timeout', ignore_result=True, soft_time_limit=10)
def call_timeout(call_uuid: str):
    """Mark a call as missed if still ringing after timeout."""
    from .call_models import Call
    from .models import Message

    try:
        call = Call.objects.get(uuid=call_uuid)
    except Call.DoesNotExist:
        return

    if call.status != 'ringing':
        return

    call.status = 'missed'
    call.ended_at = timezone.now()
    call.save(update_fields=['status', 'ended_at'])

    system_user = User.objects.get(username='system')
    body = f'Appel manqu\u00e9 de {call.initiator.username}'
    Message.objects.create(
        conversation=call.conversation,
        author=system_user,
        body=body,
        body_html=f'<p>{body}</p>',
    )

    notify_sse_inline(
        event='chat.call.missed',
        data={'call_id': str(call.uuid), 'conversation_id': str(call.conversation_id)},
        user_id=call.initiator_id,
    )

    logger.info("Call %s timed out (missed)", call_uuid)


@shared_task(name='chat.cleanup_stale_participants', ignore_result=True, soft_time_limit=30)
def cleanup_stale_participants():
    """Placeholder for stale participant cleanup."""
    pass
