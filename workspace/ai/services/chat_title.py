"""Conversation auto-title generation (body of ``ai.generate_conversation_title``)."""

import logging

from django.conf import settings

from workspace.ai.services.llm import call_llm

logger = logging.getLogger(__name__)


def generate_title(conversation_id: str) -> dict:
    """Generate a short title for *conversation_id* based on its first messages.

    No-op if the conversation already has a title or has no messages yet.
    Uses the small model with a tight system prompt to get a single-line
    title back.
    """
    from workspace.chat.models import Conversation, Message
    from workspace.chat.services.notifications import notify_conversation_members

    try:
        conversation = Conversation.objects.get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return {'status': 'error', 'error': 'Conversation not found'}

    if conversation.title:
        return {'status': 'skipped', 'reason': 'already has title'}

    messages = list(
        Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        ).order_by('created_at').values_list('body', flat=True)[:6]
    )
    if not messages:
        return {'status': 'skipped', 'reason': 'no messages'}

    excerpt = '\n'.join(m for m in messages if m)

    try:
        result = call_llm(
            [
                {
                    'role': 'system',
                    'content': (
                        'Generate a short title (max 6 words) for this conversation. '
                        'Reply with ONLY the title, no quotes, no punctuation at the end.'
                    ),
                },
                {'role': 'user', 'content': excerpt},
            ],
            model=settings.AI_SMALL_MODEL,
            max_tokens=2048,
        )
        title = result['content'].strip().strip('"\'')
        if title:
            conversation.title = title[:255]
            conversation.save(update_fields=['title'])
            notify_conversation_members(conversation)
        return {'status': 'ok', 'title': title}
    except Exception as e:
        logger.exception('Title generation failed: conversation=%s', conversation_id)
        return {'status': 'error', 'error': str(e)}
