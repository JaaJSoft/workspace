import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workspace.ai.services.conversation_history import (
    SUMMARY_BUFFER,
    build_conversation_history,
)
from workspace.ai.services.llm import (
    call_llm,
    clean_llm_content,
    sanitize_messages_for_storage,
    serialize_response,
)
from workspace.ai.services.responses import (
    handle_generation_error,
    post_bot_message,
)
from workspace.ai.services.tool_loop import run_tool_loop

logger = logging.getLogger(__name__)


@shared_task(name='ai.generate_chat_response', bind=True, max_retries=0)
def generate_chat_response(self, conversation_id: str, message_id: str, bot_user_id: int):
    """Generate a bot response in a chat conversation."""
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

        # Auto-retry once if the model returned an empty response
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

        # Guard: check if the task was cancelled while we were waiting for OpenAI
        ai_task.refresh_from_db(fields=['status'])
        if ai_task.status == AITask.Status.FAILED:
            logger.info('Bot response cancelled: conversation=%s', conversation_id)
            return {'status': 'cancelled'}

        body, bot_message = post_bot_message(
            conversation, bot_user, result, used_tools, tool_context, ai_task,
            raw_messages, tool_data=tool_data,
        )

        # Auto-generate title if the conversation doesn't have one yet
        msg_count = Message.objects.filter(
            conversation_id=conversation_id, deleted_at__isnull=True,
        ).count()
        if not conversation.title and msg_count >= 2:
            generate_conversation_title.delay(str(conversation_id))

        # Trigger rolling summary update when old messages exceed the recent window
        _recent = settings.AI_CHAT_CONTEXT_SIZE
        if msg_count > _recent:
            from workspace.ai.models import ConversationSummary
            _cs = ConversationSummary.objects.filter(conversation_id=conversation_id).first()
            needs_summary = not summary_text
            if not needs_summary and _cs and _cs.up_to:
                unsummarized = Message.objects.filter(
                    conversation_id=conversation_id,
                    deleted_at__isnull=True,
                    created_at__gt=_cs.up_to,
                ).count()
                needs_summary = unsummarized > _recent + SUMMARY_BUFFER
            if needs_summary:
                update_conversation_summary.delay(str(conversation_id))

        logger.info('Bot response generated: conversation=%s tokens=%s+%s',
                     conversation_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Bot response failed: conversation=%s', conversation_id)
        handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.update_conversation_summary', bind=True, max_retries=0)
def update_conversation_summary(self, conversation_id: str):
    """Update the rolling summary for a bot conversation.

    Summarises messages that fall outside the recent window using the small
    model, then stores the result on the ``ConversationSummary`` so future
    responses can include the condensed context instead of raw old messages.
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

    # Only fetch messages that need summarising (after last summary, up to cutoff)
    new_qs = msg_qs.filter(created_at__lte=cutoff_time).order_by('created_at')
    if conv_summary and conv_summary.up_to:
        new_qs = new_qs.filter(created_at__gt=conv_summary.up_to)

    new_messages = list(
        new_qs.select_related('author', 'author__bot_profile')
    )
    if not new_messages:
        return {'status': 'skipped', 'reason': 'no new messages to summarize'}

    # Format messages - truncate individually to keep the summarisation prompt lean
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
                conversation_id,
            )
            return {'status': 'error', 'error': 'Empty summary from model'}

        ConversationSummary.objects.update_or_create(
            conversation_id=conversation_id,
            defaults={'content': summary_content, 'up_to': cutoff_time},
        )

        logger.info(
            'Conversation summary updated: conversation=%s messages_summarized=%d tokens=%s+%s',
            conversation_id, len(new_messages),
            result['prompt_tokens'], result['completion_tokens'],
        )
        return {'status': 'ok'}

    except Exception as e:
        logger.exception('Conversation summary failed: conversation=%s', conversation_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.summarize', bind=True, max_retries=0)
def summarize(self, task_id: str):
    """Summarize a mail message."""
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_summarize_messages
    from workspace.core.sse_registry import notify_sse
    from workspace.mail.models import MailMessage

    try:
        ai_task = AITask.objects.get(pk=task_id)
    except AITask.DoesNotExist:
        logger.error('Summarize task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    try:
        message = MailMessage.objects.get(
            pk=ai_task.input_data['message_id'],
            account__owner=ai_task.owner,
        )
    except MailMessage.DoesNotExist:
        ai_task.status = AITask.Status.FAILED
        ai_task.error = 'Mail message not found'
        ai_task.completed_at = timezone.now()
        ai_task.save()
        return {'status': 'error', 'error': 'Mail message not found'}

    body = message.body_text or message.body_html or ''
    messages = build_summarize_messages(message.subject or '', body)

    try:
        result = call_llm(messages, model=settings.AI_SMALL_MODEL)
        with transaction.atomic():
            ai_task.status = AITask.Status.COMPLETED
            ai_task.result = result['content']
            ai_task.model_used = result['model']
            ai_task.prompt_tokens = result['prompt_tokens']
            ai_task.completion_tokens = result['completion_tokens']
            ai_task.raw_messages = {
                'messages': sanitize_messages_for_storage(messages),
                'response': serialize_response(result),
            }
            ai_task.completed_at = timezone.now()
            ai_task.save()

            message.ai_summary = result['content']
            message.save(update_fields=['ai_summary'])

        notify_sse('ai', ai_task.owner_id)

        logger.info('Summarize complete: task=%s tokens=%s+%s',
                     task_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Summarize failed: task=%s', task_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.editor_action', bind=True, max_retries=0)
def editor_action(self, task_id: str):
    """Run an AI action on editor content (improve, explain, summarize, custom)."""
    from workspace.ai.models import AITask
    from workspace.ai.prompts.editor import (
        build_custom_messages,
        build_explain_messages,
        build_improve_messages,
        build_summarize_messages,
    )
    from workspace.core.sse_registry import notify_sse

    try:
        ai_task = AITask.objects.get(pk=task_id)
    except AITask.DoesNotExist:
        logger.error('Editor action task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    action = ai_task.input_data.get('action', '')
    content = ai_task.input_data.get('content', '')
    language = ai_task.input_data.get('language', '')
    filename = ai_task.input_data.get('filename', '')

    builders = {
        'improve': lambda: build_improve_messages(content, language, filename),
        'explain': lambda: build_explain_messages(content, language, filename),
        'summarize': lambda: build_summarize_messages(content, language, filename),
        'custom': lambda: build_custom_messages(
            content, ai_task.input_data.get('instructions', ''), language, filename,
        ),
    }

    builder = builders.get(action)
    if not builder:
        ai_task.status = AITask.Status.FAILED
        ai_task.error = f'Unknown action: {action}'
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': f'Unknown action: {action}'}

    try:
        messages = builder()
        result = call_llm(messages)
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = result['content']
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
        ai_task.raw_messages = {
            'messages': sanitize_messages_for_storage(messages),
            'response': serialize_response(result),
        }
        ai_task.completed_at = timezone.now()
        ai_task.save()

        notify_sse('ai', ai_task.owner_id)

        logger.info('Editor action complete: task=%s action=%s tokens=%s+%s',
                     task_id, action, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Editor action failed: task=%s action=%s', task_id, action)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.compose_email', bind=True, max_retries=0)
def compose_email(self, task_id: str):
    """Compose or reply to an email."""
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_compose_messages, build_reply_messages
    from workspace.core.sse_registry import notify_sse
    from workspace.mail.models import MailAccount, MailMessage

    try:
        ai_task = AITask.objects.get(pk=task_id, owner__isnull=False)
    except AITask.DoesNotExist:
        logger.error('Compose task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    instructions = ai_task.input_data.get('instructions', '')
    original_message_id = ai_task.input_data.get('message_id')

    # Resolve sender identity from the mail account or user profile
    sender_name = ''
    sender_email = ''
    account_id = ai_task.input_data.get('account_id')
    if account_id:
        account = MailAccount.objects.filter(pk=account_id, owner=ai_task.owner).first()
        if account:
            sender_name = account.display_name
            sender_email = account.email
    if not sender_email:
        sender_name = ai_task.owner.get_full_name()
        sender_email = ai_task.owner.email or ''

    try:
        if original_message_id:
            message = MailMessage.objects.select_related('account').get(
                pk=original_message_id,
                account__owner=ai_task.owner,
            )
            body = message.body_text or message.body_html or ''
            # Use the account from the original message for reply
            reply_name = message.account.display_name or sender_name
            reply_email = message.account.email or sender_email
            messages = build_reply_messages(
                instructions, message.subject or '', body,
                sender_name=reply_name, sender_email=reply_email,
            )
        else:
            context = ai_task.input_data.get('context', '')
            messages = build_compose_messages(
                instructions, context,
                sender_name=sender_name, sender_email=sender_email,
            )

        result = call_llm(messages)
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = result['content']
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
        ai_task.raw_messages = {
            'messages': sanitize_messages_for_storage(messages),
            'response': serialize_response(result),
        }
        ai_task.completed_at = timezone.now()
        ai_task.save()

        notify_sse('ai', ai_task.owner_id)

        logger.info('Compose complete: task=%s tokens=%s+%s',
                     task_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Compose failed: task=%s', task_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}


@shared_task(name='ai.generate_conversation_title', bind=True, max_retries=0)
def generate_conversation_title(self, conversation_id: str):
    """Generate a short title for a bot conversation based on the first exchange."""
    from workspace.chat.models import Conversation, Message
    from workspace.chat.services.notifications import notify_conversation_members

    try:
        conversation = Conversation.objects.get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return {'status': 'error', 'error': 'Conversation not found'}

    # Only generate if no title set yet
    if conversation.title:
        return {'status': 'skipped', 'reason': 'already has title'}

    # Grab the first few messages for context
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


@shared_task(name='ai.purge_ai_tasks', bind=True, max_retries=0)
def purge_ai_tasks(self):
    """Delete completed AI tasks older than AI_TASK_RETENTION_DAYS."""
    from datetime import timedelta

    from workspace.ai.models import AITask

    retention_days = getattr(settings, 'AI_TASK_RETENTION_DAYS', 90)
    cutoff = timezone.now() - timedelta(days=retention_days)

    qs = AITask.objects.filter(created_at__lte=cutoff)
    count = qs.count()

    if not count:
        logger.info('AI task purge: nothing to delete.')
        return {'deleted': 0, 'retention_days': retention_days}

    logger.info('AI task purge: deleting %d tasks older than %d days', count, retention_days)
    qs.delete()

    logger.info('AI task purge complete.')
    return {'deleted': count, 'retention_days': retention_days}


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
    """Generate a bot response for a scheduled message."""
    from django.contrib.auth import get_user_model

    from workspace.ai.models import AITask, BotProfile, ScheduledMessage
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation, Message
    from workspace.chat.services.notifications import notify_new_message

    User = get_user_model()

    # Load the schedule and advance it immediately to prevent duplicate dispatches
    try:
        schedule = ScheduledMessage.objects.get(pk=schedule_id)
    except ScheduledMessage.DoesNotExist:
        logger.error('Scheduled message not found: %s', schedule_id)
        return {'status': 'error', 'error': 'Schedule not found'}

    if not schedule.is_active:
        return {'status': 'skipped', 'reason': 'inactive'}

    from workspace.users.services.settings import get_user_timezone
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

    # Inject the scheduled action instruction into the system prompt
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

        # Auto-retry once if the model returned an empty response
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

        # Let the bot skip if it judges the message is no longer relevant
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

        # Trigger rolling summary update if needed
        _recent = settings.AI_CHAT_CONTEXT_SIZE
        msg_count = Message.objects.filter(
            conversation_id=conversation.pk, deleted_at__isnull=True,
        ).count()
        if msg_count > _recent:
            from workspace.ai.models import ConversationSummary
            _cs = ConversationSummary.objects.filter(conversation_id=conversation.pk).first()
            needs_summary = not summary_text
            if not needs_summary and _cs and _cs.up_to:
                unsummarized = Message.objects.filter(
                    conversation_id=conversation.pk,
                    deleted_at__isnull=True,
                    created_at__gt=_cs.up_to,
                ).count()
                needs_summary = unsummarized > _recent + SUMMARY_BUFFER
            if needs_summary:
                update_conversation_summary.delay(str(conversation.pk))

        logger.info('Scheduled response generated: schedule=%s conversation=%s tokens=%s+%s',
                     schedule_id, conversation.pk, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Scheduled response failed: schedule=%s', schedule_id)
        handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}


CLASSIFY_BATCH_SIZE = 10
MAX_LABELS_PER_MESSAGE = 3


@shared_task(name='ai.classify_mail', bind=True, max_retries=0)
def classify_mail_messages(self, task_id: str):
    """Classify mail messages by assigning user-defined labels."""
    import orjson as _orjson
    from collections import defaultdict
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_classify_messages
    from workspace.core.sse_registry import notify_sse
    from workspace.mail.models import MailLabel, MailMessage, MailMessageLabel

    try:
        ai_task = AITask.objects.get(pk=task_id)
    except AITask.DoesNotExist:
        logger.error('Classify task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}

    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    message_uuids = ai_task.input_data.get('message_uuids', [])
    msgs = list(
        MailMessage.objects.filter(
            uuid__in=message_uuids,
            account__owner=ai_task.owner,
        ).only('uuid', 'subject', 'from_address', 'snippet', 'account_id')
    )

    if not msgs:
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = 'No messages to classify'
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'ok', 'task_id': task_id}

    # Group messages by account
    msgs_by_account = defaultdict(list)
    for m in msgs:
        msgs_by_account[m.account_id].append(m)

    total_prompt = 0
    total_completion = 0
    model_used = ''

    try:
        for account_id, account_msgs in msgs_by_account.items():
            account_labels = list(MailLabel.objects.filter(account_id=account_id))
            label_names = [lbl.name for lbl in account_labels]
            label_by_lower = {lbl.name.lower(): lbl for lbl in account_labels}

            for batch_start in range(0, len(account_msgs), CLASSIFY_BATCH_SIZE):
                batch = account_msgs[batch_start:batch_start + CLASSIFY_BATCH_SIZE]
                uuid_index = {i + 1: m for i, m in enumerate(batch)}

                emails = []
                for m in batch:
                    from_addr = m.from_address if isinstance(m.from_address, dict) else {}
                    emails.append({
                        'subject': m.subject or '',
                        'from_name': from_addr.get('name', ''),
                        'from_email': from_addr.get('email', ''),
                        'snippet': m.snippet or '',
                    })

                messages = build_classify_messages(emails, label_names)
                result = call_llm(messages, model=settings.AI_SMALL_MODEL)

                model_used = result['model']
                total_prompt += result['prompt_tokens'] or 0
                total_completion += result['completion_tokens'] or 0

                try:
                    items = _orjson.loads(result['content'])
                except (ValueError, TypeError):
                    logger.warning('Classify: malformed JSON response for task %s', task_id)
                    raise ValueError('Malformed JSON response from LLM')

                if not isinstance(items, list):
                    raise ValueError('Expected JSON array from LLM')

                links_to_create = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    idx = item.get('i')
                    raw_labels = item.get('labels', [])

                    msg = uuid_index.get(idx)
                    if not msg:
                        continue

                    if not isinstance(raw_labels, list):
                        continue

                    count = 0
                    for raw_name in raw_labels:
                        if count >= MAX_LABELS_PER_MESSAGE:
                            break
                        if not isinstance(raw_name, str):
                            continue
                        label = label_by_lower.get(raw_name.lower())
                        if label:
                            links_to_create.append(
                                MailMessageLabel(message=msg, label=label)
                            )
                            count += 1

                if links_to_create:
                    MailMessageLabel.objects.bulk_create(links_to_create, ignore_conflicts=True)

        with transaction.atomic():
            ai_task.status = AITask.Status.COMPLETED
            ai_task.result = f'Classified {len(msgs)} messages'
            ai_task.model_used = model_used
            ai_task.prompt_tokens = total_prompt
            ai_task.completion_tokens = total_completion
            ai_task.completed_at = timezone.now()
            ai_task.save()
        notify_sse('ai', ai_task.owner_id)

        logger.info('Classify complete: task=%s messages=%d tokens=%d+%d',
                     task_id, len(msgs), total_prompt, total_completion)
        return {'status': 'ok', 'task_id': task_id}

    except Exception as e:
        logger.exception('Classify failed: task=%s', task_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        return {'status': 'error', 'error': str(e)}
