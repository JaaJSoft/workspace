"""Record the Celery tasks a block of code sends, with the priority of each."""

from contextlib import contextmanager
from unittest.mock import patch

from celery.app.task import Task


@contextmanager
def queued_tasks(run=()):
    """Yield the list of ``(task name, priority)`` sent inside the block.

    Nothing reaches a broker. The priority is the one Celery publishes: the
    call's ``priority=``, else the task's declared one. A task named in *run*
    also runs in place, so what it sends in turn is recorded as well.
    """
    sent = []

    def apply_async(task, args=None, kwargs=None, **options):
        sent.append((task.name, options.get("priority", task.priority)))
        if task.name in run:
            task.run(*(args or ()), **(kwargs or {}))

    with patch.object(Task, "apply_async", autospec=True, side_effect=apply_async):
        yield sent
