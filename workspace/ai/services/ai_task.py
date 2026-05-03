"""AITask lifecycle helper.

Wraps the boilerplate "load AITask + set PROCESSING + try/except + set
COMPLETED/FAILED + notify_sse" that 4 of the AI tasks duplicate identically.
Used by the standard-pattern tasks (summarize, editor_action, compose_email,
classify_mail). Tasks with custom lifecycles (generate_chat_response,
generate_scheduled_response) keep their own state management because they
also create their AITask inside the task body and use post_bot_message
for response posting.
"""

import logging
from contextlib import contextmanager

from django.utils import timezone

logger = logging.getLogger(__name__)


@contextmanager
def ai_task_lifecycle(task_id, *, log_label):
    """Wrap an AITask through PROCESSING -> COMPLETED/FAILED transitions.

    Yields the loaded AITask. The caller assigns ``result``, ``model_used``,
    ``prompt_tokens``, ``completion_tokens``, ``raw_messages`` etc. on the
    yielded instance; ``status``, ``completed_at`` and the SSE notification
    are set on context exit.

    On exception inside the with-block, the AITask is moved to FAILED with
    ``error`` set to ``str(e)``, the exception is logged, then re-raised so
    the caller can convert it to its own response shape.

    Caller is responsible for catching ``AITask.DoesNotExist`` outside the
    with-block (the helper has no opinion on the response format used to
    signal "task not found").

    Example:
        try:
            with ai_task_lifecycle(task_id, log_label='Editor action') as ai_task:
                result = call_llm(build_messages(ai_task.input_data))
                ai_task.result = result['content']
                ai_task.model_used = result['model']
                ai_task.prompt_tokens = result['prompt_tokens']
                ai_task.completion_tokens = result['completion_tokens']
            return {'status': 'ok', 'task_id': task_id}
        except AITask.DoesNotExist:
            return {'status': 'error', 'error': 'Task not found'}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
    """
    from workspace.ai.models import AITask
    from workspace.core.sse_registry import notify_sse

    ai_task = AITask.objects.get(pk=task_id)
    ai_task.status = AITask.Status.PROCESSING
    ai_task.save(update_fields=['status'])

    try:
        yield ai_task
    except Exception as e:
        logger.exception('%s failed: task=%s', log_label, task_id)
        ai_task.status = AITask.Status.FAILED
        ai_task.error = str(e)
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
        raise
    else:
        # If the body explicitly set FAILED for an in-band validation
        # error (e.g. unknown action), respect that. Otherwise mark
        # COMPLETED.
        if ai_task.status != AITask.Status.FAILED:
            ai_task.status = AITask.Status.COMPLETED
        ai_task.completed_at = timezone.now()
        ai_task.save()
        notify_sse('ai', ai_task.owner_id)
