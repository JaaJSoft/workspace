"""AgentGoal writes that must not reopen a closed goal.

A goal is written by the user (the chat UI), by the agent (its tools, during a
check-in) and by the check-in worker, often within the same minute. Once one
of them closes it - completed or abandoned - the others' writes must not land:
a stale "resume" or a notes update would bring it back ACTIVE, and the
dispatcher would start waking the agent up again. Every write of the goal's
own fields therefore goes through :func:`update_open_goal`.
"""

from django.utils import timezone

from workspace.ai.models import AgentGoal

OPEN_STATUSES = (AgentGoal.Status.ACTIVE, AgentGoal.Status.PAUSED)


def update_open_goal(goal, values):
    """Write the *values* dict on *goal* if it is still open; whether it was.

    A dict rather than keyword arguments: ``goal`` is also one of the fields.
    The instance takes the values only when they landed.
    """
    values = {**values, "updated_at": timezone.now()}
    updated = AgentGoal.objects.filter(pk=goal.pk, status__in=OPEN_STATUSES).update(
        **values
    )
    if not updated:
        return False
    for field, value in values.items():
        setattr(goal, field, value)
    return True


def close_goal(goal, status, outcome):
    """Close an open *goal* as *status* with *outcome*; False if it was closed."""
    return update_open_goal(
        goal, {"status": status, "outcome": outcome, "closed_at": timezone.now()}
    )
