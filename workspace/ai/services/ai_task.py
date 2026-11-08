"""AITask lifecycle: the transitions every worker goes through.

A task moves PENDING -> PROCESSING -> COMPLETED or FAILED, and several actors
write those transitions at once: the worker running it, a duplicate Celery
delivery of the same job, the user cancelling from the chat. Each transition
is therefore a conditional ``UPDATE`` on the status it expects, and whoever
lands first wins - :func:`claim_task` for the start, :func:`close_task` for the
end. A loser learns it from the return value and must stop, not post.

:func:`ai_task_lifecycle` wraps both for the standard-pattern tasks
(summarize, editor_action, compose_email, classify_mail, extract). Tasks with
custom lifecycles (generate_chat_response, generate_scheduled_response,
run_agent_goal_check) call the two helpers themselves and post their result
through post_bot_message.
"""

import logging
from contextlib import contextmanager

from django.utils import timezone

from workspace.ai.models import AITask
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

OPEN_STATUSES = (AITask.Status.PENDING, AITask.Status.PROCESSING)

# What a run records on its task besides the transition itself.
OUTCOME_FIELDS = (
    "result",
    "error",
    "model_used",
    "prompt_tokens",
    "completion_tokens",
    "generation_seconds",
    "answer_tokens",
    "answer_seconds",
    "raw_messages",
)


class TaskNotPending(Exception):
    """The task was claimed or closed before this run could claim it."""


def claim_task(ai_task):
    """Move *ai_task* from PENDING to PROCESSING; False when it was not pending.

    A redelivered job finds the row PROCESSING or already closed, and a cancel
    that landed while the job waited in the queue has closed it: neither may
    run the task again.
    """
    claimed = AITask.objects.filter(pk=ai_task.pk, status=AITask.Status.PENDING).update(
        status=AITask.Status.PROCESSING
    )
    if claimed:
        ai_task.status = AITask.Status.PROCESSING
    return bool(claimed)


def close_task(ai_task, status, **values):
    """Close *ai_task* as *status*, writing *values* with it, unless it is closed.

    Returns whether this call closed it. On False the row keeps what the
    winner wrote - a user cancel, or the run a redelivery duplicated - and
    the caller must not act on its own result. The instance is updated only
    when the call wins.
    """
    values = {"status": status, "completed_at": timezone.now(), **values}
    closed = AITask.objects.filter(pk=ai_task.pk, status__in=OPEN_STATUSES).update(
        **values
    )
    if not closed:
        return False
    for field, value in values.items():
        setattr(ai_task, field, value)
    return True


@contextmanager
def ai_task_lifecycle(task_id, *, log_label):
    """Run an AITask through PROCESSING -> COMPLETED/FAILED.

    Yields the loaded AITask. The caller assigns ``result``, ``model_used``,
    ``prompt_tokens``, ``completion_tokens``, ``raw_messages`` etc. on the
    yielded instance; the transition and the SSE notification happen on
    context exit, and write the :data:`OUTCOME_FIELDS` alone.

    Raises :class:`TaskNotPending` on entry, before the body runs, when the
    task is not PENDING any more: a duplicate delivery of a job that already
    ran must not run it twice.

    On exception inside the with-block, the AITask is moved to FAILED with
    ``error`` set to ``str(e)``, the exception is logged, then re-raised so
    the caller can convert it to its own response shape.

    Caller is responsible for catching ``AITask.DoesNotExist`` and
    ``TaskNotPending`` outside the with-block (the helper has no opinion on
    the response format used to signal them).

    Example:
        try:
            with ai_task_lifecycle(task_id, log_label='Editor action') as ai_task:
                result = call_llm(build_messages(ai_task.input_data))
                ai_task.result = result['content']
                ai_task.model_used = result['model']
            return {'status': 'ok', 'task_id': task_id}
        except AITask.DoesNotExist:
            return {'status': 'error', 'error': 'Task not found'}
        except TaskNotPending:
            return {'status': 'skipped', 'reason': 'not_pending'}
    """
    from workspace.core.sse_registry import notify_sse

    ai_task = AITask.objects.get(pk=task_id)
    if not claim_task(ai_task):
        logger.info(
            "%s skipped, task no longer pending: task=%s", log_label, scrub(task_id)
        )
        raise TaskNotPending(task_id)

    try:
        yield ai_task
    except Exception as e:
        logger.exception("%s failed: task=%s", log_label, scrub(task_id))
        # What the body recorded before failing stays: the raw reply of a
        # malformed answer is what explains the error.
        outcome = {field: getattr(ai_task, field) for field in OUTCOME_FIELDS}
        outcome["error"] = str(e)
        if close_task(ai_task, AITask.Status.FAILED, **outcome):
            notify_sse("ai", ai_task.owner_id)
        raise
    else:
        # If the body explicitly set FAILED for an in-band validation
        # error (e.g. unknown action), respect that. Otherwise mark
        # COMPLETED.
        status = (
            AITask.Status.FAILED
            if ai_task.status == AITask.Status.FAILED
            else AITask.Status.COMPLETED
        )
        outcome = {field: getattr(ai_task, field) for field in OUTCOME_FIELDS}
        if close_task(ai_task, status, **outcome):
            notify_sse("ai", ai_task.owner_id)


def failed_task_count(since):
    """Number of tasks that failed since *since*.

    Filters on ``completed_at`` (stamped when the task turns FAILED), not
    ``created_at``: a long-queued task that fails today is today's failure.
    """
    return AITask.objects.filter(
        status=AITask.Status.FAILED, completed_at__gte=since
    ).count()
