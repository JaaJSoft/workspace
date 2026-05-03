"""Mail composition AI (body of the ``ai.compose_email`` Celery task)."""

import logging

from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import (
    call_llm,
    sanitize_messages_for_storage,
    serialize_response,
)

logger = logging.getLogger(__name__)


def compose_mail(task_id: str) -> dict:
    """Compose a new email or generate a reply to an existing one.

    Resolves the sender identity from the requested mail account (or falls
    back to the user profile), builds the appropriate prompt (compose vs
    reply), then writes the LLM result back to the AITask for the UI to
    poll.
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_compose_messages, build_reply_messages
    from workspace.mail.models import MailAccount, MailMessage

    try:
        with ai_task_lifecycle(task_id, log_label='Compose') as ai_task:
            instructions = ai_task.input_data.get('instructions', '')
            original_message_id = ai_task.input_data.get('message_id')

            # Resolve sender identity from the mail account or user profile.
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

            if original_message_id:
                message = MailMessage.objects.select_related('account').get(
                    pk=original_message_id,
                    account__owner=ai_task.owner,
                )
                body = message.body_text or message.body_html or ''
                # Use the account from the original message for reply.
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
            ai_task.result = result['content']
            ai_task.model_used = result['model']
            ai_task.prompt_tokens = result['prompt_tokens']
            ai_task.completion_tokens = result['completion_tokens']
            ai_task.raw_messages = {
                'messages': sanitize_messages_for_storage(messages),
                'response': serialize_response(result),
            }

            logger.info('Compose complete: task=%s tokens=%s+%s',
                        task_id, result['prompt_tokens'], result['completion_tokens'])
            return {'status': 'ok', 'task_id': task_id}
    except AITask.DoesNotExist:
        logger.error('Compose task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}
