"""Atomic row-claim helpers for Celery dispatcher/worker patterns.

Background — the recurring race
-------------------------------

Many of our Celery jobs follow the same shape: a periodic *dispatcher*
task queries "rows that are due" (events to fire, feeds to re-fetch,
scheduled messages to send, …) and fans out a per-row *worker* task via
``.delay()``. Without coordination this pattern has **two distinct
races** that produce user-visible duplicates:

1. **Dispatcher race.** Two dispatcher runs overlapping (celery-beat
   misfire, scaled-out scheduler, restart during a tick) both see the
   same row as due and both enqueue the worker. The user gets the side
   effect twice.

2. **Worker race / duplicate Celery delivery.** A worker dies between
   processing the message and acking it, or Celery decides to redeliver
   for any other reason. Two workers run against the same row, both
   read the same "due" state, both commit the side effect.

The fix is the same in both cases: **compare-and-swap (CAS) on a
timestamp field** that distinguishes "due" from "claimed". The
dispatcher CAS-advances the field past the due window before
enqueueing; the worker CAS-pins its own update against the exact value
the dispatcher wrote, so a duplicate delivery whose row has been
finalized by the winning worker matches zero rows and bails.

The CAS itself is two lines of Django ORM, but getting all of —
``is_active`` predicate, ``NULL`` handling for never-claimed rows,
rolling back the claim on broker failure, passing the token to the
worker, ISO-(de)serialising it, falling back when the token is absent
(manual trigger / test) — right at every call site is what produces
bugs. This module hides those details.

Pattern usage
-------------

Dispatcher - use :func:`dispatch_due`, which is the whole loop::

    outcome = dispatch_due(
        ScheduledMessage.objects.filter(
            is_active=True, next_run_at__lte=timezone.now()
        ).only('pk', 'next_run_at'),
        generate_scheduled_response,
        claim_field='next_run_at',
        extra_where={'is_active': True},
        label='scheduled message',
        log=logger,
    )

Only the *due predicate* stays in the calling module, because only that
module knows what "due" means for its rows. Everything after it - the
claim, the rollback on broker failure, continuing past one bad row - is
identical everywhere and lives here.

Worker::

    if not cas_finalize(
        Model, pk, claim_field='next_run_at', claim_token=claim_token,
        updates={'last_run_at': ..., 'next_run_at': new_next_run},
        extra_where={'is_active': True},
    ):
        return {'status': 'skipped', 'reason': 'already_claimed'}

A ``claim_token`` of ``None`` makes ``cas_finalize`` skip the CAS check
— this is the legacy / direct-test path, where the dispatcher window is
absent and there is no token to verify.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# How far forward the dispatcher pushes the claim field when it claims a
# row. Any worker that fails to finalize its claim within this window
# leaves the row to re-fire naturally on the next dispatcher pass —
# self-healing fallback if the Celery task is lost or the worker dies
# before its own CAS update.
DISPATCH_LOCK_HORIZON = timedelta(hours=1)


def cas_claim(
    model,
    pk,
    claim_field,
    observed_value,
    *,
    extra_where=None,
    lock_horizon=DISPATCH_LOCK_HORIZON,
):
    """CAS-claim a row by parking ``claim_field`` at a future token.

    Returns the token (a timezone-aware ``datetime``) if the claim
    succeeded, or ``None`` if a concurrent dispatcher beat us to it. The
    caller is expected to pass ``token.isoformat()`` to the worker so
    the worker can :func:`cas_finalize` against the same value.
    """
    token = timezone.now() + lock_horizon
    where = (
        {f"{claim_field}__isnull": True}
        if observed_value is None
        else {claim_field: observed_value}
    )
    if extra_where:
        where.update(extra_where)
    updated = model.objects.filter(pk=pk, **where).update(**{claim_field: token})
    return token if updated else None


def cas_rollback(model, pk, claim_field, original_value):
    """Restore ``claim_field`` to its pre-claim value.

    Used after a broker error so the row stays due and re-fires on the
    next dispatcher pass instead of being parked at the claim token for
    :data:`DISPATCH_LOCK_HORIZON`. Unconditional ``update`` is fine here
    — by the time we reach this branch we owned the claim, and rolling
    back is the right thing even if a worker has somehow already begun
    (it will lose its CAS in :func:`cas_finalize`).
    """
    model.objects.filter(pk=pk).update(**{claim_field: original_value})


def cas_finalize(
    model,
    pk,
    claim_field,
    claim_token,
    updates,
    *,
    extra_where=None,
):
    """CAS-update a row keyed on the dispatcher's claim token.

    Returns ``True`` if exactly one row was updated, ``False`` if zero
    — i.e. another worker has already finalized this claim with the
    same token, or the row no longer matches ``extra_where``.

    ``claim_token`` may be a ``datetime`` or its ``.isoformat()``
    string. ``None`` skips the CAS predicate entirely: this is the
    legacy / manual-trigger path where the dispatcher's window is
    absent, and the caller is asserting it doesn't need the protection.
    """
    where = {}
    if claim_token is not None:
        expected = (
            datetime.fromisoformat(claim_token)
            if isinstance(claim_token, str)
            else claim_token
        )
        where[claim_field] = expected
    if extra_where:
        where.update(extra_where)
    return model.objects.filter(pk=pk, **where).update(**updates) == 1


@dataclass(frozen=True)
class DispatchOutcome:
    """What one dispatcher pass did.

    ``already_claimed`` counts rows that looked due in the SELECT but whose
    CAS lost - a competing dispatcher got there first, or the row stopped
    matching ``extra_where`` in between. It is normally zero; a persistently
    non-zero value means dispatcher passes are overlapping.
    """

    dispatched: int = 0
    already_claimed: int = 0

    def as_dict(self):
        """Celery-serialisable form, for tasks that return their outcome."""
        return {
            "dispatched": self.dispatched,
            "already_claimed": self.already_claimed,
        }


def dispatch_due(
    due,
    worker,
    *,
    claim_field,
    extra_where=None,
    lock_horizon=DISPATCH_LOCK_HORIZON,
    label=None,
    log=None,
):
    """Claim each row of *due* and enqueue *worker* for it.

    This is the dispatcher half of the pattern this module documents. It was
    reimplemented per module before, and the copies drifted: some counted
    what they dispatched, some did not, and the rollback-on-broker-failure
    branch is the kind of detail that is easy to omit in the fourth copy.

    *due* is a queryset the caller builds, because the due predicate is
    domain knowledge (``next_run_at <= now`` is not ``last_sync_at`` being
    stale). Restrict it with ``.only('pk', <claim_field>)``: those are the
    only attributes read here, and leaving the claim field deferred would
    cost a query per row.

    *worker* is a Celery task called as ``worker.delay(str(row.pk), token)``.
    Every model that uses this pattern has its UUID as its primary key, so
    ``pk`` is the UUID the worker looks itself up by.

    Returns a :class:`DispatchOutcome`. Never raises for a single row: a
    broker error rolls that row's claim back so it stays due for the next
    pass, and the loop continues so one bad row cannot cost every later row
    its turn.
    """
    model = due.model
    log = log or logger
    label = label or model.__name__
    dispatched = 0
    already_claimed = 0

    for row in due:
        original = getattr(row, claim_field)
        token = cas_claim(
            model,
            row.pk,
            claim_field=claim_field,
            observed_value=original,
            extra_where=extra_where,
            lock_horizon=lock_horizon,
        )
        if token is None:
            already_claimed += 1
            continue
        try:
            worker.delay(str(row.pk), token.isoformat())
        except Exception:
            # Roll the claim back so the row stays due and re-fires on the
            # next pass, instead of sitting parked at the token for the whole
            # lock horizon. Keep looping: the remaining rows are unaffected.
            cas_rollback(model, row.pk, claim_field, original)
            log.exception("Failed to enqueue %s: row=%s", label, scrub(str(row.pk)))
            continue
        dispatched += 1

    return DispatchOutcome(dispatched=dispatched, already_claimed=already_claimed)
