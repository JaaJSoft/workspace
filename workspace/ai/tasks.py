import base64
import json
import logging
import re

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r'<think>[\s\S]*?</think>\s*', re.IGNORECASE)
_RAW_TOOL_CALL_RE = re.compile(r'</?tool_call>', re.IGNORECASE)
_JSON_TOOL_RE = re.compile(
    r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}[^{}]*\}',
)


def _build_tool_content(tool_result: str):
    """Convert a tool result string into API content, handling image payloads."""
    try:
        parsed = json.loads(tool_result)
        if isinstance(parsed, dict) and parsed.get('type') == 'image':
            mime = parsed.get('mime_type', 'image/webp')
            data = parsed['data']
            return [
                {'type': 'text', 'text': 'Here is the image:'},
                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}},
            ]
    except (json.JSONDecodeError, KeyError):
        pass
    return tool_result


def _strip_thinking(content: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    return _THINK_RE.sub('', content).strip()


def _extract_raw_tool_calls(content: str):
    """Parse tool calls that a model emitted as plain text instead of structured output.

    Some models output ``<tool_call>{"name": "...", "arguments": {...}}</tool_call>``
    or just bare JSON in the message content.  Return a list of (name, arguments_json)
    tuples and the remaining text, or (None, content) if nothing was found.
    """
    # Strip <tool_call> tags first
    cleaned = _RAW_TOOL_CALL_RE.sub('', content).strip()

    # Try to find a JSON object with "name" and "arguments" anywhere in the text
    match = _JSON_TOOL_RE.search(cleaned)
    if not match:
        return None, content

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None, content

    if isinstance(parsed, dict) and 'name' in parsed and 'arguments' in parsed:
        args = parsed['arguments']
        args_json = args if isinstance(args, str) else json.dumps(args)
        remaining = (cleaned[:match.start()] + cleaned[match.end():]).strip()
        return [(parsed['name'], args_json)], remaining
    return None, content


def _call_openai(messages: list[dict], model: str | None = None, max_tokens: int | None = None, tools: list | None = None) -> dict:
    """Call the OpenAI API and return a dict with content and usage info."""
    from workspace.ai.client import get_ai_client

    client = get_ai_client()
    if not client:
        raise RuntimeError('AI is not configured (AI_API_KEY missing)')

    kwargs = {
        'model': model or settings.AI_MODEL,
        'messages': messages,
        'max_tokens': max_tokens or settings.AI_MAX_TOKENS,
    }
    if tools:
        kwargs['tools'] = tools

    response = client.chat.completions.create(**kwargs)

    choice = response.choices[0]
    content = _strip_thinking(choice.message.content or '')
    return {
        'content': content,
        'tool_calls': choice.message.tool_calls,
        'message': choice.message,
        'model': response.model,
        'prompt_tokens': response.usage.prompt_tokens if response.usage else None,
        'completion_tokens': response.usage.completion_tokens if response.usage else None,
    }


def _track_tool_usage(tool_call, tool_result, used_tools):
    """Extract a human-readable detail from a successful tool call."""
    from workspace.ai.tool_registry import tool_registry
    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, AttributeError):
        args = {}
    used_tools.append((name, tool_registry.get_detail(name, args)))


def _render_tool_badges(used_tools):
    """Render HTML badges for tools used during response generation."""
    from workspace.ai.tool_registry import tool_registry

    grouped = {}
    for name, detail in used_tools:
        grouped.setdefault(name, [])
        if detail:
            grouped[name].append(detail)

    parts = []
    for name, details in grouped.items():
        cfg = tool_registry.get_badge(name)
        icon = cfg['icon']
        label = cfg['label']
        if details:
            details_display = ' &bull; '.join(details)
            parts.append(f'<span>{icon}</span> {label}: {details_display}')
        else:
            parts.append(f'<span>{icon}</span> {label}')

    badges_html = ' <span class="opacity-30">|</span> '.join(parts)
    return (
        f'\n<div class="mt-2 text-xs text-base-content/40 flex items-center gap-1 flex-wrap">'
        f'{badges_html}'
        f'</div>'
    )


def _run_tool_loop(messages, model, supports_tools, human_user, bot_user, conversation_id):
    """Run the tool call loop and return (result, used_tools, tool_context).

    Calls the AI model, executes any tool calls it returns, and re-calls
    until we get a plain text response (max 5 rounds).
    """
    from workspace.ai.tool_registry import tool_registry

    tools = tool_registry.get_definitions() if supports_tools else None
    result = _call_openai(messages, model=model, tools=tools)

    used_tools = []
    tool_context = {}
    max_tool_rounds = 5
    for _ in range(max_tool_rounds):
        # Some models emit tool calls as plain text — parse and convert them
        if not result.get('tool_calls') and result.get('content'):
            raw_calls, remaining = _extract_raw_tool_calls(result['content'])
            if raw_calls:
                import types
                import uuid as _uuid
                result['content'] = remaining
                result['tool_calls'] = []
                for name, args_json in raw_calls:
                    call_id = f'call_{_uuid.uuid4().hex[:24]}'
                    tc = types.SimpleNamespace(
                        id=call_id,
                        type='function',
                        function=types.SimpleNamespace(name=name, arguments=args_json),
                    )
                    result['tool_calls'].append(tc)
                _content = remaining or None
                _tool_calls = result['tool_calls']
                result['message'] = types.SimpleNamespace(
                    content=_content,
                    tool_calls=_tool_calls,
                    role='assistant',
                )
                result['message'].to_dict = lambda: {
                    'role': 'assistant',
                    'content': _content or '',
                    'tool_calls': [
                        {'id': tc.id, 'type': 'function', 'function': {'name': tc.function.name, 'arguments': tc.function.arguments}}
                        for tc in _tool_calls
                    ],
                }

        if not result.get('tool_calls'):
            break

        messages.append(result['message'].to_dict())

        for tc in result['tool_calls']:
            tool_result = tool_registry.execute(
                tc, user=human_user, bot=bot_user,
                conversation_id=conversation_id, context=tool_context,
            )
            tool_content = _build_tool_content(tool_result)
            messages.append({
                'role': 'tool',
                'tool_call_id': tc.id,
                'content': tool_content,
            })
            if 'Error' not in tool_result and 'Unknown tool' not in tool_result:
                _track_tool_usage(tc, tool_result, used_tools)

        result = _call_openai(messages, model=model, tools=tools)

    return result, used_tools, tool_context


def _post_bot_message(conversation, bot_user, result, used_tools, tool_context, ai_task):
    """Create the bot message, attach images, update unread counts, notify, and complete AITask.

    Returns (body, bot_message).
    """
    from django.core.files.base import ContentFile
    from django.db.models import F

    from workspace.chat.models import ConversationMember, Message, MessageAttachment
    from workspace.chat.services import notify_conversation_members, render_message_body

    body = _RAW_TOOL_CALL_RE.sub('', result['content']).strip()
    body_html = render_message_body(body)

    if used_tools:
        body_html += _render_tool_badges(used_tools)

    bot_message = Message.objects.create(
        conversation_id=conversation.pk,
        author=bot_user,
        body=body,
        body_html=body_html,
    )

    # Attach any images generated by tools during this response
    pending = tool_context.get('images', [])
    for i, img in enumerate(pending):
        slug = img.get('prompt', 'image')[:60].strip().replace(' ', '_')
        slug = ''.join(c for c in slug if c.isalnum() or c in '_-')
        suffix = f'_{i + 1}' if len(pending) > 1 else ''
        filename = f'{slug}{suffix}.png'
        att = MessageAttachment(
            message=bot_message,
            original_name=filename,
            mime_type='image/png',
            size=len(img['data']),
        )
        att.file.save(filename, ContentFile(img['data']), save=False)
        att.save()

    ConversationMember.objects.filter(
        conversation_id=conversation.pk,
        left_at__isnull=True,
    ).exclude(user=bot_user).update(
        unread_count=F('unread_count') + 1,
    )

    from workspace.chat.models import Conversation as Conv
    Conv.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())

    notify_conversation_members(conversation, exclude_user=bot_user)

    ai_task.status = ai_task.Status.COMPLETED
    ai_task.result = body
    ai_task.chat_message = bot_message
    ai_task.model_used = result['model']
    ai_task.prompt_tokens = result['prompt_tokens']
    ai_task.completion_tokens = result['completion_tokens']
    ai_task.completed_at = timezone.now()
    ai_task.save()

    return body, bot_message


def _handle_generation_error(conversation, bot_user, ai_task, error):
    """Handle a failed bot response: post error message, update counts, notify."""
    from django.db.models import F

    from workspace.chat.models import ConversationMember, Message
    from workspace.chat.services import notify_conversation_members, render_message_body

    ai_task.status = ai_task.Status.FAILED
    ai_task.error = str(error)
    ai_task.completed_at = timezone.now()
    ai_task.save()

    error_body = f"⚠️ Sorry, I encountered an error: {error}"
    error_html = render_message_body(error_body)
    Message.objects.create(
        conversation_id=conversation.pk,
        author=bot_user,
        body=error_body,
        body_html=error_html,
    )
    ConversationMember.objects.filter(
        conversation_id=conversation.pk,
        left_at__isnull=True,
    ).exclude(user=bot_user).update(
        unread_count=F('unread_count') + 1,
    )
    from workspace.chat.models import Conversation as Conv
    Conv.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
    notify_conversation_members(conversation, exclude_user=bot_user)


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

    recent_messages = (
        Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )
        .select_related('author', 'author__bot_profile')
        .prefetch_related('attachments')
        .order_by('-created_at')[:settings.AI_CHAT_CONTEXT_SIZE]
    )
    # Find the most recent user message that has image attachments
    last_image_msg_uuid = None
    if bot_profile.supports_vision:
        for msg in recent_messages:  # newest first
            is_bot = hasattr(msg.author, 'bot_profile')
            if not is_bot and any(att.is_image for att in msg.attachments.all()):
                last_image_msg_uuid = str(msg.uuid)
                break

    history = []
    for msg in reversed(recent_messages):
        is_bot = hasattr(msg.author, 'bot_profile')
        role = 'assistant' if is_bot else 'user'

        # Include images only from the most recent message that has images
        image_parts = []
        if not is_bot and str(msg.uuid) == last_image_msg_uuid:
            for att in msg.attachments.all():
                if att.is_image:
                    try:
                        data = att.file.read()
                        b64 = base64.b64encode(data).decode()
                        image_parts.append({
                            'type': 'image_url',
                            'image_url': {
                                'url': f'data:{att.mime_type};base64,{b64}',
                            },
                        })
                    except Exception:
                        logger.warning('Could not read attachment %s', att.uuid)

        if image_parts:
            content = []
            if msg.body:
                content.append({'type': 'text', 'text': msg.body})
            content.extend(image_parts)
            history.append({'role': role, 'content': content})
        else:
            history.append({'role': role, 'content': msg.body})

    bot_name = bot_user.get_full_name() or bot_user.username

    # Identify the human user who triggered the response
    trigger_message = Message.objects.filter(pk=message_id).select_related('author').first()
    human_user = trigger_message.author if trigger_message else None

    messages = build_chat_messages(
        bot_profile.system_prompt, history, bot_name=bot_name,
        user=human_user, bot=bot_user,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={'conversation_id': conversation_id, 'message_id': message_id},
    )

    try:
        result, used_tools, tool_context = _run_tool_loop(
            messages, bot_profile.get_model(), bot_profile.supports_tools,
            human_user, bot_user, conversation_id,
        )

        # Auto-retry once if the model returned an empty response
        body_preview = _RAW_TOOL_CALL_RE.sub('', result.get('content') or '').strip()
        if not body_preview and not tool_context.get('images'):
            logger.warning('Empty response, retrying once: conversation=%s', conversation_id)
            result, used_tools, tool_context = _run_tool_loop(
                messages, bot_profile.get_model(), bot_profile.supports_tools,
                human_user, bot_user, conversation_id,
            )
            body_preview = _RAW_TOOL_CALL_RE.sub('', result.get('content') or '').strip()
            if not body_preview and not tool_context.get('images'):
                raise RuntimeError('Empty response from model')

        # Guard: check if the task was cancelled while we were waiting for OpenAI
        ai_task.refresh_from_db(fields=['status'])
        if ai_task.status == AITask.Status.FAILED:
            logger.info('Bot response cancelled: conversation=%s', conversation_id)
            return {'status': 'cancelled'}

        body, bot_message = _post_bot_message(
            conversation, bot_user, result, used_tools, tool_context, ai_task,
        )

        # Auto-generate title if the conversation doesn't have one yet
        if not conversation.title and Message.objects.filter(
            conversation_id=conversation_id, deleted_at__isnull=True,
        ).count() >= 2:
            generate_conversation_title.delay(str(conversation_id))

        logger.info('Bot response generated: conversation=%s tokens=%s+%s',
                     conversation_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Bot response failed: conversation=%s', conversation_id)
        _handle_generation_error(conversation, bot_user, ai_task, e)
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
        result = _call_openai(messages, model=settings.AI_SMALL_MODEL)
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = result['content']
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
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
        result = _call_openai(messages)
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = result['content']
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
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

        result = _call_openai(messages)
        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = result['content']
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
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
    from workspace.chat.services import notify_conversation_members

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
        result = _call_openai(
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
    from workspace.chat.services import notify_new_message

    User = get_user_model()

    # Load the schedule and advance it immediately to prevent duplicate dispatches
    try:
        schedule = ScheduledMessage.objects.get(pk=schedule_id)
    except ScheduledMessage.DoesNotExist:
        logger.error('Scheduled message not found: %s', schedule_id)
        return {'status': 'error', 'error': 'Schedule not found'}

    if not schedule.is_active:
        return {'status': 'skipped', 'reason': 'inactive'}

    from workspace.users.settings_service import get_user_timezone
    creator_tz = get_user_timezone(schedule.created_by)

    schedule.last_run_at = timezone.now()
    schedule.compute_next_run(user_tz=creator_tz)
    schedule.save(update_fields=['last_run_at', 'next_run_at', 'is_active'])

    try:
        bot_user = User.objects.get(pk=schedule.bot_id)
        bot_profile = BotProfile.objects.get(user=bot_user)
        conversation = Conversation.objects.get(pk=schedule.conversation_id)
    except (User.DoesNotExist, BotProfile.DoesNotExist, Conversation.DoesNotExist):
        logger.error('Scheduled response failed: schedule=%s — bot or conversation not found', schedule_id)
        return {'status': 'error', 'error': 'Not found'}

    # Load recent conversation context (simplified — no vision/image handling)
    recent_messages = (
        Message.objects.filter(
            conversation_id=conversation.pk,
            deleted_at__isnull=True,
        )
        .select_related('author', 'author__bot_profile')
        .order_by('-created_at')[:settings.AI_CHAT_CONTEXT_SIZE]
    )

    history = []
    for msg in reversed(recent_messages):
        is_bot = hasattr(msg.author, 'bot_profile')
        role = 'assistant' if is_bot else 'user'
        history.append({'role': role, 'content': msg.body})

    bot_name = bot_user.get_full_name() or bot_user.username
    human_user = User.objects.filter(pk=schedule.created_by_id).first()

    # Inject the scheduled action instruction into the system prompt
    scheduled_instruction = (
        f'\n\n## Scheduled action\n'
        f'You previously scheduled a proactive message with the following instruction:\n'
        f'"{schedule.prompt}"\n\n'
        f'Now is the time to act on it. Generate an appropriate message for the user.\n'
        f'Be natural — do not mention that you are a scheduled message.\n'
        f'If, based on the conversation context, you judge that this message is no longer '
        f'relevant or useful (e.g. the topic was already addressed, the event has passed, '
        f'the user already handled it), reply with exactly "[SKIP]" and nothing else.'
    )

    messages = build_chat_messages(
        bot_profile.system_prompt + scheduled_instruction,
        history, bot_name=bot_name,
        user=human_user, bot=bot_user,
    )

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={'schedule_id': schedule_id, 'conversation_id': str(conversation.pk)},
    )

    try:
        result, used_tools, tool_context = _run_tool_loop(
            messages, bot_profile.get_model(), bot_profile.supports_tools,
            human_user, bot_user, str(conversation.pk),
        )

        # Let the bot skip if it judges the message is no longer relevant
        body = _RAW_TOOL_CALL_RE.sub('', result['content']).strip()
        if body == '[SKIP]':
            ai_task.status = ai_task.Status.COMPLETED
            ai_task.result = '[SKIP]'
            ai_task.model_used = result['model']
            ai_task.prompt_tokens = result['prompt_tokens']
            ai_task.completion_tokens = result['completion_tokens']
            ai_task.completed_at = timezone.now()
            ai_task.save()
            logger.info('Scheduled response skipped (bot judged irrelevant): schedule=%s', schedule_id)
            return {'status': 'skipped', 'reason': 'bot_judged_irrelevant'}

        body, bot_message = _post_bot_message(
            conversation, bot_user, result, used_tools, tool_context, ai_task,
        )

        notify_new_message(conversation, bot_user, body)

        logger.info('Scheduled response generated: schedule=%s conversation=%s tokens=%s+%s',
                     schedule_id, conversation.pk, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Scheduled response failed: schedule=%s', schedule_id)
        _handle_generation_error(conversation, bot_user, ai_task, e)
        return {'status': 'error', 'error': str(e)}
