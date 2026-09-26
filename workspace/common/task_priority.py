"""The Celery priority scale every task declares with ``@shared_task(priority=...)``.

On Redis, kombu keeps each priority step (0, 3, 6, 9) in a list of its own and
the worker drains the lower numbers first; a value between two steps is
floored to the step below, which is why the scale uses the steps themselves.
Priorities only reorder what is waiting: they never preempt a running task,
and a worker process may already have reserved one task beyond the one it runs
(CELERY_WORKER_PREFETCH_MULTIPLIER), which runs before anything that arrives
meanwhile.

A task sent without a priority gets 0, the top step, so a task that forgets
to declare one jumps ahead of every chat reply - core.tests.test_task_priorities
fails on any task left without one.

Each level below is picked from who waits for the result and how long they
can wait. A task whose callers differ (a manual "sync now" next to the
periodic sync) declares the level of its common path, and the call site that
differs passes ``priority=`` to ``apply_async``.
"""

# Someone is on a screen waiting for this result right now (a spinner, a
# message that just went out). Seconds matter.
INTERACTIVE_PRIORITY = 0

# The follow-up of a user action or of a due time, which the user notices when
# it lands minutes late: a thumbnail, a search document, a mail poll, a
# reminder.
NORMAL_PRIORITY = 3

# Periodic reconciliation and long bulk jobs nobody watches closely. Minutes of
# delay cost nothing, and a job that re-enqueues itself slice by slice lets
# everything above it through between two slices.
LOW_PRIORITY = 6

# Housekeeping and backlogs: purges, catch-up passes. A backlog sent here runs
# after every task already waiting, whatever its size.
BACKGROUND_PRIORITY = 9

TASK_PRIORITIES = (
    INTERACTIVE_PRIORITY,
    NORMAL_PRIORITY,
    LOW_PRIORITY,
    BACKGROUND_PRIORITY,
)
