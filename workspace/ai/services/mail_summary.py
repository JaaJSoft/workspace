"""Mail message AI summarization (body of the ``ai.summarize`` Celery task)."""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import (
    call_llm,
    sanitize_messages_for_storage,
    serialize_response,
)

logger = logging.getLogger(__name__)


def summarize_mail(task_id: str) -> dict:
    """Summarize a single mail message and persist the result.

    Loads the AITask, fetches the referenced MailMessage, calls the LLM
    with the small model, then writes the summary back to both the AITask
    (for history) and the MailMessage.ai_summary field (for display).
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_summarize_messages
    from workspace.mail.models import MailMessage

    try:
        with ai_task_lifecycle(task_id, log_label='Summarize') as ai_task:
            try:
                message = MailMessage.objects.get(
                    pk=ai_task.input_data['message_id'],
                    account__owner=ai_task.owner,
                )
            except MailMessage.DoesNotExist:
                ai_task.status = AITask.Status.FAILED
                ai_task.error = 'Mail message not found'
                return {'status': 'error', 'error': 'Mail message not found'}

            body = message.body_text or message.body_html or ''
            messages = build_summarize_messages(message.subject or '', body)
            result = call_llm(messages, model=settings.AI_SMALL_MODEL)

            with transaction.atomic():
                ai_task.result = result['content']
                ai_task.model_used = result['model']
                ai_task.prompt_tokens = result['prompt_tokens']
                ai_task.completion_tokens = result['completion_tokens']
                ai_task.raw_messages = {
                    'messages': sanitize_messages_for_storage(messages),
                    'response': serialize_response(result),
                }
                # ``ai_task_lifecycle`` will set status=COMPLETED + completed_at
                # on context exit. We need to save the message inside the
                # atomic block though.
                message.ai_summary = result['content']
                message.save(update_fields=['ai_summary'])

            logger.info('Summarize complete: task=%s tokens=%s+%s',
                        task_id, result['prompt_tokens'], result['completion_tokens'])
            return {'status': 'ok', 'task_id': task_id}
    except AITask.DoesNotExist:
        logger.error('Summarize task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}
