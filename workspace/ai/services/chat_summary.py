"""Conversation rolling-summary logic.

Owns both the actual summarisation work (body of
``ai.update_conversation_summary``) and the dispatch policy that decides
when to fire that task from another bot-response handler.
"""

import logging

from django.conf import settings
from django.db.models import Q

from workspace.ai.services.llm import call_llm
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Re-summarise when the count of unsummarised old messages exceeds the recent
# window by this many. The buffer absorbs the cost of the summary task so it
# does not fire on every single new message.
SUMMARY_BUFFER = 10


def update_summary(conversation_id: str) -> dict:
    """Compute and persist the rolling summary for *conversation_id*.

    Summarises messages outside the recent window using the small model.
    The result is stored on ``ConversationSummary`` with ``up_to`` pointing
    at the cutoff message, so the next invocation only needs to summarise
    the messages that arrived since.
    """
    from workspace.ai.models import ConversationSummary
    from workspace.chat.models import Conversation, Message

    if not Conversation.objects.filter(pk=conversation_id).exists():
        return {'status': 'error', 'error': 'Conversation not found'}

    recent_window = settings.AI_CHAT_CONTEXT_SIZE

    msg_qs = Message.objects.filter(
        conversation_id=conversation_id,
        deleted_at__isnull=True,
    )
    total = msg_qs.count()
    if total <= recent_window:
        return {'status': 'skipped', 'reason': 'not enough messages'}

    # The cutoff is the newest message outside the recent window (i.e. the
    # first one that should be summarised rather than kept verbatim).
    cutoff_msg = (
        msg_qs.order_by('-created_at')
        .values_list('created_at', flat=True)[recent_window:recent_window + 1]
    )
    cutoff_time = list(cutoff_msg)[0] if cutoff_msg else None
    if not cutoff_time:
        return {'status': 'skipped', 'reason': 'could not determine cutoff'}

    conv_summary = ConversationSummary.objects.filter(conversation_id=conversation_id).first()

    # Already up-to-date?
    if conv_summary and conv_summary.up_to and conv_summary.up_to >= cutoff_time:
        return {'status': 'skipped', 'reason': 'already up to date'}

    # Only fetch messages that need summarising (after last summary, up to cutoff).
    new_qs = msg_qs.filter(created_at__lte=cutoff_time).order_by('created_at')
    if conv_summary and conv_summary.up_to:
        new_qs = new_qs.filter(created_at__gt=conv_summary.up_to)

    new_messages = list(
        new_qs.select_related('author', 'author__bot_profile')
    )
    if not new_messages:
        return {'status': 'skipped', 'reason': 'no new messages to summarize'}

    # Format messages - truncate individually to keep the summarisation prompt lean.
    lines = []
    for msg in new_messages:
        name = msg.author.get_full_name() or msg.author.username
        is_bot = hasattr(msg.author, 'bot_profile')
        label = f'[Bot] {name}' if is_bot else name
        body = msg.body[:1000] if len(msg.body) > 1000 else msg.body
        lines.append(f'{label}: {body}')

    messages_text = '\n'.join(lines)

    system = (
        'Summarize this conversation concisely. Preserve:\n'
        '- Key topics discussed and conclusions reached\n'
        '- User preferences, personal details, and requests\n'
        '- Ongoing tasks or commitments\n'
        '- Important context needed to continue the conversation naturally\n\n'
        'Write in the same language as the conversation. Be concise but complete.'
    )

    existing = conv_summary.content if conv_summary else ''
    if existing:
        user_content = f'Previous summary:\n{existing}\n\nNew messages to incorporate:\n{messages_text}'
    else:
        user_content = f'Messages:\n{messages_text}'

    prompt_messages = [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user_content},
    ]

    try:
        result = call_llm(
            prompt_messages,
            model=settings.AI_SMALL_MODEL or settings.AI_MODEL,
            max_tokens=4096,
        )
        summary_content = result['content']
        if not summary_content:
            logger.warning(
                'Empty summary from model (tokens=%s+%s), skipping: conversation=%s',
                result.get('prompt_tokens'), result.get('completion_tokens'),
                scrub(conversation_id),
            )
            return {'status': 'error', 'error': 'Empty summary from model'}

        # Guard ``up_to`` so it can only move forward: if two summary jobs for
        # the same conversation overlap, the older one (with an older
        # cutoff_time) must not overwrite a newer summary. ``upsert`` with a
        # filter that excludes "newer than us" rows keeps the write monotonic.
        updated = ConversationSummary.objects.filter(
            conversation_id=conversation_id,
        ).filter(Q(up_to__isnull=True) | Q(up_to__lte=cutoff_time)).update(
            content=summary_content, up_to=cutoff_time,
        )
        if not updated:
            # Either the row does not exist yet, or a more recent summary won
            # the race. Try to create the row; if it already exists with a
            # newer up_to, leave it alone.
            ConversationSummary.objects.get_or_create(
                conversation_id=conversation_id,
                defaults={'content': summary_content, 'up_to': cutoff_time},
            )

        logger.info(
            'Conversation summary updated: conversation=%s messages_summarized=%d tokens=%s+%s',
            scrub(conversation_id), len(new_messages),
            result['prompt_tokens'], result['completion_tokens'],
        )
        return {'status': 'ok'}

    except Exception as e:
        logger.exception('Conversation summary failed: conversation=%s', scrub(conversation_id))
        return {'status': 'error', 'error': str(e)}


def maybe_dispatch_summary_update(conversation_id, summary_text: str) -> bool:
    """Decide whether to fire ``ai.update_conversation_summary`` and dispatch if so.

    Called after a bot response is posted. Fires the summary task when:
    - the conversation has more messages than the recent window, AND
    - either no summary exists yet, OR the count of messages newer than
      ``ConversationSummary.up_to`` exceeds the window by ``SUMMARY_BUFFER``.

    Returns True if the task was dispatched.
    """
    from workspace.ai.models import ConversationSummary
    from workspace.chat.models import Message

    recent = settings.AI_CHAT_CONTEXT_SIZE
    msg_count = Message.objects.filter(
        conversation_id=conversation_id, deleted_at__isnull=True,
    ).count()
    if msg_count <= recent:
        return False

    needs_summary = not summary_text
    if not needs_summary:
        cs = ConversationSummary.objects.filter(conversation_id=conversation_id).first()
        if cs and cs.up_to:
            unsummarized = Message.objects.filter(
                conversation_id=conversation_id,
                deleted_at__isnull=True,
                created_at__gt=cs.up_to,
            ).count()
            needs_summary = unsummarized > recent + SUMMARY_BUFFER

    if needs_summary:
        # Lazy import to avoid a circular dependency: tasks.py imports this
        # service module (via the thin wrapper), but only at call time, so
        # tasks.py is fully loaded by the time we re-enter it here.
        from workspace.ai.tasks import update_conversation_summary
        update_conversation_summary.delay(str(conversation_id))
        return True
    return False
