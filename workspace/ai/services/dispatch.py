"""Single entry point to create an AITask and trigger its Celery worker.

Never call ``AITask.objects.create()`` from application code directly:
go through :func:`dispatch`. Creating the row and enqueuing the worker
in one place guarantees the two stay in sync (a row without a worker
hangs forever in ``PENDING``; a worker call with no row throws inside
``ai_task_lifecycle``), and keeps the task_type -> worker mapping in a
single spot so adding a new AI feature is one entry instead of a grep
across views.
"""

from workspace.ai.models import AITask


def dispatch(*, owner, task_type: str, input_data: dict | None = None) -> AITask:
    """Create an AITask row and enqueue the matching Celery worker.

    Raises ``ValueError`` if ``task_type`` has no mapped worker. Gating
    on ``is_ai_enabled()`` and per-user settings is the caller's job:
    contexts differ (auto-sync respects the user setting, a manual
    admin trigger may bypass it).
    """
    ai_task = AITask.objects.create(
        owner=owner,
        task_type=task_type,
        input_data=input_data or {},
    )
    _enqueue_worker(ai_task)
    return ai_task


def _enqueue_worker(ai_task: AITask) -> None:
    # Imports live inside the function to avoid an import cycle:
    # ``workspace.ai.tasks`` imports from ``workspace.ai.services``
    # (e.g. ``ai_task_lifecycle``), so importing tasks at module
    # load time would form a loop.
    from workspace.ai.tasks.calendar import extract_from_mail_messages
    from workspace.ai.tasks.editor import editor_action
    from workspace.ai.tasks.mail import (
        classify_mail_messages,
        compose_email,
        summarize,
    )

    mapping = {
        AITask.TaskType.SUMMARIZE: summarize,
        AITask.TaskType.COMPOSE: compose_email,
        AITask.TaskType.REPLY: compose_email,
        AITask.TaskType.CLASSIFY: classify_mail_messages,
        AITask.TaskType.EDITOR: editor_action,
        AITask.TaskType.EXTRACT: extract_from_mail_messages,
    }
    worker = mapping.get(ai_task.task_type)
    if worker is None:
        raise ValueError(
            f'No Celery worker mapped for AITask.task_type={ai_task.task_type!r}'
        )
    worker.delay(str(ai_task.uuid))
