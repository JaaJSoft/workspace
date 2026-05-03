"""Bot response generation in a chat conversation.

Body of ``ai.generate_chat_response``. Loads conversation context, builds
the prompt, runs the tool loop (with one retry on empty response), posts
the bot message, and triggers follow-up tasks (auto-title + rolling
summary update) when warranted.
"""

import logging

from workspace.ai.services.chat_summary import maybe_dispatch_summary_update
from workspace.ai.services.conversation_history import build_conversation_history
from workspace.ai.services.llm import (
    clean_llm_content,
    sanitize_messages_for_storage,
)
from workspace.ai.services.responses import handle_generation_error, post_bot_message
from workspace.ai.services.tool_loop import run_tool_loop

logger = logging.getLogger(__name__)


def generate_response(conversation_id: str, message_id: str, bot_user_id: int) -> dict:
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
        logger.error('Bot response failed: conversation=%s bot=%s not found', conversation_id, bot_user_id)
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
        body_preview = clean_llm_content(result.get('content') or '')
        if not body_preview and not tool_context.get('images'):
            logger.warning('Empty response, retrying once: conversation=%s', conversation_id)
            result, used_tools, tool_context, retry_rounds, retry_td = run_tool_loop(
                messages, bot_profile.get_model(),
                human_user, bot_user, conversation_id,
            )
            rounds.extend(retry_rounds)
            if retry_td:
                tool_data = (tool_data or []) + retry_td
            body_preview = clean_llm_content(result.get('content') or '')
            if not body_preview and not tool_context.get('images'):
                raise RuntimeError('Empty response from model')

        raw_messages = {'messages': initial_messages, 'rounds': rounds}

        # Guard: check if the task was cancelled while we were waiting for OpenAI.
        ai_task.refresh_from_db(fields=['status'])
        if ai_task.status == AITask.Status.FAILED:
            logger.info('Bot response cancelled: conversation=%s', conversation_id)
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
            from workspace.ai.tasks import generate_conversation_title
            generate_conversation_title.delay(str(conversation_id))

        maybe_dispatch_summary_update(conversation_id, summary_text)

        logger.info('Bot response generated: conversation=%s tokens=%s+%s',
                    conversation_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Bot response failed: conversation=%s', conversation_id)
        handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}
