"""Scheduled bot message Celery tasks (dispatcher + worker)."""

import logging

from celery import shared_task
from django.utils import timezone

from workspace.ai.services.chat_summary import maybe_dispatch_summary_update
from workspace.ai.services.conversation_history import build_conversation_history
from workspace.ai.services.llm import (
    clean_llm_content,
    sanitize_messages_for_storage,
)
from workspace.ai.services.responses import handle_generation_error, post_bot_message
from workspace.ai.services.tool_loop import run_tool_loop

logger = logging.getLogger(__name__)


@shared_task(name='ai.dispatch_scheduled_messages')
def dispatch_scheduled_messages():
    """Find due scheduled messages and dispatch a generation task for each."""
    from workspace.ai.models import ScheduledMessage

    now = timezone.now()
    due = ScheduledMessage.objects.filter(is_active=True, next_run_at__lte=now)
    count = 0
    for schedule in due:
        generate_scheduled_response.delay(str(schedule.uuid))
        count += 1
    if count:
        logger.info('Dispatched %d scheduled message(s)', count)


@shared_task(name='ai.generate_scheduled_response', bind=True, max_retries=0)
def generate_scheduled_response(self, schedule_id: str):
    """Run a scheduled bot message: load schedule, advance, generate.

    Advances the schedule's ``next_run_at`` immediately to prevent duplicate
    dispatches if the worker takes a while. The bot may emit ``[SKIP]`` to
    indicate that the scheduled action is no longer relevant; in that case
    no message is posted.
    """
    from django.contrib.auth import get_user_model

    from workspace.ai.models import AITask, BotProfile, ScheduledMessage
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation
    from workspace.chat.services.notifications import notify_new_message
    from workspace.users.services.settings import get_user_timezone

    User = get_user_model()

    # Load the schedule and advance it immediately to prevent duplicate dispatches.
    try:
        schedule = ScheduledMessage.objects.get(pk=schedule_id)
    except ScheduledMessage.DoesNotExist:
        logger.error('Scheduled message not found: %s', schedule_id)
        return {'status': 'error', 'error': 'Schedule not found'}

    if not schedule.is_active:
        return {'status': 'skipped', 'reason': 'inactive'}

    creator_tz = get_user_timezone(schedule.created_by)

    schedule.last_run_at = timezone.now()
    schedule.compute_next_run(user_tz=creator_tz)
    schedule.save(update_fields=['last_run_at', 'next_run_at', 'is_active'])

    try:
        bot_user = User.objects.get(pk=schedule.bot_id)
        bot_profile = BotProfile.objects.get(user=bot_user)
        conversation = Conversation.objects.get(pk=schedule.conversation_id)
    except (User.DoesNotExist, BotProfile.DoesNotExist, Conversation.DoesNotExist):
        logger.error('Scheduled response failed: schedule=%s - bot or conversation not found', schedule_id)
        return {'status': 'error', 'error': 'Not found'}

    human_user = User.objects.filter(pk=schedule.created_by_id).first()

    history, summary_text = build_conversation_history(
        str(conversation.pk), bot_profile, human_user,
    )

    bot_name = bot_user.get_full_name() or bot_user.username

    # Inject the scheduled action instruction into the system prompt.
    scheduled_instruction = (
        f'\n\n## Scheduled action\n'
        f'You previously scheduled a proactive message with the following instruction:\n'
        f'"{schedule.prompt}"\n\n'
        f'Now is the time to act on it. Generate an appropriate message for the user.\n'
        f'Be natural - do not mention that you are a scheduled message.\n'
        f'If, based on the conversation context, you judge that this message is no longer '
        f'relevant or useful (e.g. the topic was already addressed, the event has passed, '
        f'the user already handled it), reply with exactly "[SKIP]" and nothing else.'
    )

    messages = build_chat_messages(
        bot_profile.system_prompt + scheduled_instruction,
        history, bot_name=bot_name,
        user=human_user, bot=bot_user, summary=summary_text,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={'schedule_id': schedule_id, 'conversation_id': str(conversation.pk)},
    )

    try:
        initial_messages = sanitize_messages_for_storage(list(messages))

        result, used_tools, tool_context, rounds, tool_data = run_tool_loop(
            messages, bot_profile.get_model(),
            human_user, bot_user, str(conversation.pk),
        )

        # Auto-retry once if the model returned an empty response.
        body_preview = clean_llm_content(result.get('content') or '')
        if not body_preview and not tool_context.get('images'):
            logger.warning('Empty scheduled response, retrying once: schedule=%s', schedule_id)
            result, used_tools, tool_context, retry_rounds, retry_td = run_tool_loop(
                messages, bot_profile.get_model(),
                human_user, bot_user, str(conversation.pk),
            )
            rounds.extend(retry_rounds)
            if retry_td:
                tool_data = (tool_data or []) + retry_td
            body_preview = clean_llm_content(result.get('content') or '')
            if not body_preview and not tool_context.get('images'):
                ai_task.status = ai_task.Status.COMPLETED
                ai_task.result = '[EMPTY]'
                ai_task.model_used = result.get('model', '')
                ai_task.prompt_tokens = result.get('prompt_tokens')
                ai_task.completion_tokens = result.get('completion_tokens')
                ai_task.completed_at = timezone.now()
                ai_task.save()
                logger.warning('Scheduled response empty after retry: schedule=%s', schedule_id)
                return {'status': 'skipped', 'reason': 'empty_response'}

        raw_messages = {'messages': initial_messages, 'rounds': rounds}

        # Let the bot skip if it judges the message is no longer relevant.
        body = clean_llm_content(result['content'])
        if body == '[SKIP]':
            ai_task.status = ai_task.Status.COMPLETED
            ai_task.result = '[SKIP]'
            ai_task.model_used = result['model']
            ai_task.prompt_tokens = result['prompt_tokens']
            ai_task.completion_tokens = result['completion_tokens']
            ai_task.raw_messages = raw_messages
            ai_task.completed_at = timezone.now()
            ai_task.save()
            logger.info('Scheduled response skipped (bot judged irrelevant): schedule=%s', schedule_id)
            return {'status': 'skipped', 'reason': 'bot_judged_irrelevant'}

        body, bot_message = post_bot_message(
            conversation, bot_user, result, used_tools, tool_context, ai_task,
            raw_messages, tool_data=tool_data,
        )

        notify_new_message(conversation, bot_user, body)

        maybe_dispatch_summary_update(str(conversation.pk), summary_text)

        logger.info('Scheduled response generated: schedule=%s conversation=%s tokens=%s+%s',
                    schedule_id, conversation.pk, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Scheduled response failed: schedule=%s', schedule_id)
        handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}
