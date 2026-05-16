"""Chat-related AI Celery tasks (bot response, summary, auto-title).

The summary task body lives in ``services/chat_summary.py`` because that
module also exposes ``maybe_dispatch_summary_update``, used by both the
chat-response and scheduled-response pipelines. The other two tasks are
single-caller and live here directly.
"""

import logging

from celery import shared_task
from django.conf import settings

from workspace.ai.services.chat_summary import maybe_dispatch_summary_update
from workspace.ai.services.conversation_history import build_conversation_history
from workspace.ai.services.llm import (
    call_llm,
    clean_llm_content,
    sanitize_messages_for_storage,
)
from workspace.ai.services.responses import handle_generation_error, post_bot_message
from workspace.ai.services.tool_loop import retry_final_completion, run_tool_loop
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@shared_task(name='ai.generate_chat_response', bind=True, max_retries=0)
def generate_chat_response(self, conversation_id: str, message_id: str, bot_user_id: int):
    """Generate a bot response in *conversation_id* triggered by *message_id*.

    Creates an AITask to track progress, runs the tool loop with one
    auto-retry on empty response, then posts the bot message via
    ``post_bot_message``. Failures route to ``handle_generation_error``.
    """
    from django.contrib.auth import get_user_model

    from workspace.ai.models import AITask, BotProfile
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation, Message

    User = get_user_model()

    try:
        bot_user = User.objects.get(pk=bot_user_id)
        bot_profile = BotProfile.objects.get(user=bot_user)
        conversation = Conversation.objects.get(pk=conversation_id)
    except (User.DoesNotExist, BotProfile.DoesNotExist, Conversation.DoesNotExist):
        logger.error('Bot response failed: conversation=%s bot=%s not found',
                     scrub(conversation_id), scrub(bot_user_id))
        return {'status': 'error', 'error': 'Not found'}

    trigger_message = Message.objects.filter(pk=message_id).select_related('author').first()
    human_user = trigger_message.author if trigger_message else None

    history, summary_text = build_conversation_history(
        conversation_id, bot_profile, human_user,
    )

    bot_name = bot_user.get_full_name() or bot_user.username

    messages = build_chat_messages(
        bot_profile.system_prompt, history, bot_name=bot_name,
        user=human_user, bot=bot_user, summary=summary_text,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={'conversation_id': conversation_id, 'message_id': message_id},
    )

    try:
        initial_messages = sanitize_messages_for_storage(list(messages))

        result, used_tools, tool_context, rounds, tool_data = run_tool_loop(
            messages, bot_profile.get_model(),
            human_user, bot_user, conversation_id,
        )

        # Auto-retry once if the model returned an empty response.
        # Only the final completion is retried (no tools): rerunning
        # the whole loop would re-execute side-effectful tools and
        # discard the first pass's tool_context / used_tools.
        body_preview = clean_llm_content(result.get('content') or '')
        if not body_preview and not tool_context.get('images'):
            logger.warning('Empty response, retrying once: conversation=%s', scrub(conversation_id))
            result, retry_rounds = retry_final_completion(messages, bot_profile.get_model())
            rounds.extend(retry_rounds)
            body_preview = clean_llm_content(result.get('content') or '')
            if not body_preview and not tool_context.get('images'):
                raise RuntimeError('Empty response from model')

        raw_messages = {'messages': initial_messages, 'rounds': rounds}

        # Guard: check if the task was cancelled while we were waiting for OpenAI.
        ai_task.refresh_from_db(fields=['status'])
        if ai_task.status == AITask.Status.FAILED:
            logger.info('Bot response cancelled: conversation=%s', scrub(conversation_id))
            return {'status': 'cancelled'}

        body, bot_message = post_bot_message(
            conversation, bot_user, result, used_tools, tool_context, ai_task,
            raw_messages, tool_data=tool_data,
        )

        # Auto-generate title if the conversation doesn't have one yet.
        msg_count = Message.objects.filter(
            conversation_id=conversation_id, deleted_at__isnull=True,
        ).count()
        if not conversation.title and msg_count >= 2:
            generate_conversation_title.delay(str(conversation_id))

        maybe_dispatch_summary_update(conversation_id, summary_text)

        logger.info('Bot response generated: conversation=%s tokens=%s+%s',
                    scrub(conversation_id), result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Bot response failed: conversation=%s', scrub(conversation_id))
        handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.update_conversation_summary', bind=True, max_retries=0)
def update_conversation_summary(self, conversation_id: str):
    """Update the rolling summary for a bot conversation."""
    from workspace.ai.services.chat_summary import update_summary
    return update_summary(conversation_id)


@shared_task(name='ai.generate_conversation_title', bind=True, max_retries=0)
def generate_conversation_title(self, conversation_id: str):
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
            model=settings.AI_SMALL_MODEL or settings.AI_MODEL,
            max_tokens=2048,
        )
        title = result['content'].strip().strip('"\'')
        if title:
            conversation.title = title[:255]
            conversation.save(update_fields=['title'])
            notify_conversation_members(conversation)
        return {'status': 'ok', 'title': title}
    except Exception as e:
        logger.exception('Title generation failed: conversation=%s', scrub(conversation_id))
        return {'status': 'error', 'error': str(e)}
