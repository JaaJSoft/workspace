import base64
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _call_openai(messages: list[dict], model: str | None = None, max_tokens: int | None = None) -> dict:
    """Call the OpenAI API and return a dict with content and usage info."""
    from workspace.ai.client import get_ai_client

    client = get_ai_client()
    if not client:
        raise RuntimeError('AI is not configured (AI_API_KEY missing)')

    response = client.chat.completions.create(
        model=model or settings.AI_MODEL,
        messages=messages,
        max_tokens=max_tokens or settings.AI_MAX_TOKENS,
    )

    choice = response.choices[0]
    return {
        'content': choice.message.content or '',
        'model': response.model,
        'prompt_tokens': response.usage.prompt_tokens if response.usage else None,
        'completion_tokens': response.usage.completion_tokens if response.usage else None,
    }


@shared_task(name='ai.generate_chat_response', bind=True, max_retries=0)
def generate_chat_response(self, conversation_id: str, message_id: str, bot_user_id: int):
    """Generate a bot response in a chat conversation."""
    from django.contrib.auth import get_user_model
    from django.db.models import F

    from workspace.ai.models import AITask, BotProfile
    from workspace.ai.prompts.chat import build_chat_messages
    from workspace.chat.models import Conversation, ConversationMember, Message
    from workspace.chat.services import notify_conversation_members, render_message_body

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
        .order_by('-created_at')[:50]
    )
    # Find the most recent user message that has image attachments
    last_image_msg_uuid = None
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
    messages = build_chat_messages(bot_profile.system_prompt, history, bot_name=bot_name)

    ai_task = AITask.objects.create(
        owner=bot_user,
        task_type=AITask.TaskType.CHAT,
        status=AITask.Status.PROCESSING,
        input_data={'conversation_id': conversation_id, 'message_id': message_id},
    )

    try:
        result = _call_openai(messages, model=bot_profile.get_model())

        # Guard: check if the task was cancelled while we were waiting for OpenAI
        ai_task.refresh_from_db(fields=['status'])
        if ai_task.status == AITask.Status.FAILED:
            logger.info('Bot response cancelled: conversation=%s', conversation_id)
            return {'status': 'cancelled'}

        body = result['content']
        body_html = render_message_body(body)
        bot_message = Message.objects.create(
            conversation_id=conversation_id,
            author=bot_user,
            body=body,
            body_html=body_html,
        )

        ConversationMember.objects.filter(
            conversation_id=conversation_id,
            left_at__isnull=True,
        ).exclude(user=bot_user).update(
            unread_count=F('unread_count') + 1,
        )

        Conversation.objects.filter(pk=conversation_id).update(
            updated_at=timezone.now(),
        )

        notify_conversation_members(conversation, exclude_user=bot_user)

        ai_task.status = AITask.Status.COMPLETED
        ai_task.result = body
        ai_task.chat_message = bot_message
        ai_task.model_used = result['model']
        ai_task.prompt_tokens = result['prompt_tokens']
        ai_task.completion_tokens = result['completion_tokens']
        ai_task.completed_at = timezone.now()
        ai_task.save()

        logger.info('Bot response generated: conversation=%s tokens=%s+%s',
                     conversation_id, result['prompt_tokens'], result['completion_tokens'])
        return {'status': 'ok', 'message_id': str(bot_message.uuid)}

    except Exception as e:
        logger.exception('Bot response failed: conversation=%s', conversation_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()

        # Post a visible error message in the conversation so the user knows
        error_body = f"⚠️ Sorry, I encountered an error: {e}"
        error_html = render_message_body(error_body)
        Message.objects.create(
            conversation_id=conversation_id,
            author=bot_user,
            body=error_body,
            body_html=error_html,
        )
        ConversationMember.objects.filter(
            conversation_id=conversation_id,
            left_at__isnull=True,
        ).exclude(user=bot_user).update(
            unread_count=F('unread_count') + 1,
        )
        Conversation.objects.filter(pk=conversation_id).update(
            updated_at=timezone.now(),
        )
        notify_conversation_members(conversation, exclude_user=bot_user)

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
        result = _call_openai(messages)
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
