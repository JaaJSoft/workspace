"""Editor AI Celery tasks (improve, explain, summarize, custom)."""

import logging

from celery import shared_task

from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import (
    call_llm,
    sanitize_messages_for_storage,
    serialize_response,
)
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@shared_task(name='ai.editor_action', bind=True, max_retries=0)
def editor_action(self, task_id: str):
    """Run an AI action on editor content (improve, explain, summarize, custom).

    Reads the requested action and content from ``AITask.input_data``,
    builds the appropriate prompt, calls the LLM, and writes the result.
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.editor import (
        build_custom_messages,
        build_explain_messages,
        build_improve_messages,
        build_summarize_messages,
    )

    try:
        with ai_task_lifecycle(task_id, log_label='Editor action') as ai_task:
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
                return {'status': 'error', 'error': f'Unknown action: {action}'}

            messages = builder()
            result = call_llm(messages)
            ai_task.result = result['content']
            ai_task.model_used = result['model']
            ai_task.prompt_tokens = result['prompt_tokens']
            ai_task.completion_tokens = result['completion_tokens']
            ai_task.raw_messages = {
                'messages': sanitize_messages_for_storage(messages),
                'response': serialize_response(result),
            }

            logger.info('Editor action complete: task=%s action=%s tokens=%s+%s',
                        scrub(task_id), scrub(action),
                        result['prompt_tokens'], result['completion_tokens'])
            return {'status': 'ok', 'task_id': task_id}
    except AITask.DoesNotExist:
        logger.error('Editor action task not found: %s', scrub(task_id))
        return {'status': 'error', 'error': 'Task not found'}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}
