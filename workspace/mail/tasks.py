"""Celery tasks for mail synchronization."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from workspace.common.celery_claim import cas_claim, cas_finalize, cas_rollback
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)
User = get_user_model()


def _sync_interval():
    return getattr(settings, "MAIL_SYNC_INTERVAL", 300)


def _due_threshold():
    """Accounts not synced since this instant are due for another pass."""
    return timezone.now() - timedelta(seconds=_sync_interval())


def _lock_horizon_seconds():
    """Self-healing window shared by the claim and the in-flight lock.

    Both are recoveries from a worker that stopped existing rather than
    finishing, and both fail in the same direction (the account does not sync
    until the window elapses), so they get one number rather than two knobs
    to keep consistent.

    For the **claim** it has to cover enqueue-to-finalise (broker latency plus
    queue wait), not the sync itself, since the worker finalises before it
    connects. For the **lock** it has to cover a whole sync, but overshooting
    a long one only degrades to the duplicate IMAP pass this guard exists to
    avoid, whereas overshooting on the recovery side means real silence.

    The shared 1h default in ``celery_claim`` is sized for a 900s cadence;
    against this module's 300s it would turn a dropped message into an hour of
    silence, so scale to the interval instead.
    """
    return _sync_interval() * 4


def _lock_horizon():
    return timedelta(seconds=_lock_horizon_seconds())


@shared_task(name="mail.sync_all_accounts", bind=True, max_retries=0)
def sync_all_accounts(self):
    """Dispatch a sync task for every active account that is due.

    Each account's IMAP pass is network-bound and can take seconds to
    minutes. Running them all inline in this task serialized every account
    behind the slowest one and, once the total exceeded the beat period,
    let successive ticks stack duplicate work on the broker.

    Rows are CAS-claimed by advancing ``last_sync_at`` past the due
    threshold (see :mod:`workspace.common.celery_claim`), so only the
    dispatcher whose UPDATE affected a row enqueues its worker. Concurrent
    dispatcher runs race on the same predicate and the database guarantees
    exactly one winner per account.

    The claim does not track in-flight syncs: the worker finalises before
    connecting, so a pass outliving ``MAIL_SYNC_INTERVAL`` can still be
    enqueued a second time. Dropping that duplicate is the worker's job (it
    holds a lock for the duration of the sync), not the dispatcher's, so this
    task can stay a cheap dispatch loop and does not need to reason about
    which syncs are still running.
    """
    from workspace.mail.models import MailAccount

    threshold = _due_threshold()
    due = MailAccount.objects.filter(
        Q(last_sync_at__lt=threshold) | Q(last_sync_at__isnull=True),
        is_active=True,
    ).only("pk", "uuid", "last_sync_at")

    dispatched = 0
    skipped = 0

    for account in due:
        original = account.last_sync_at
        token = cas_claim(
            MailAccount,
            account.pk,
            claim_field="last_sync_at",
            observed_value=original,
            extra_where={"is_active": True},
            lock_horizon=_lock_horizon(),
        )
        if token is None:
            # Another dispatcher claimed this account, or it was
            # deactivated between the SELECT and the UPDATE.
            skipped += 1
            continue
        try:
            sync_single_account.delay(str(account.uuid), token.isoformat())
        except Exception:
            # Broker errors etc. - roll back the claim so the account stays
            # due and re-fires on the next pass instead of being parked at
            # the token for the lock horizon. Keep looping so the other due
            # accounts still get their chance.
            cas_rollback(MailAccount, account.pk, "last_sync_at", original)
            logger.exception(
                "Failed to enqueue mail sync: account=%s", scrub(str(account.pk))
            )
            continue
        dispatched += 1

    logger.info(
        "Mail sync dispatched: %d account(s), %d already claimed", dispatched, skipped
    )
    return {"accounts_dispatched": dispatched, "already_claimed": skipped}


@shared_task(name="mail.sync_account", bind=True, max_retries=0)
def sync_single_account(self, account_uuid, claim_token=None):
    """Sync a single mail account.

    Two guards, covering two different races, neither sufficient alone:

    - The **lock** covers a *different* token: a second dispatcher legitimately
      claimed the account because the first pass outlived the interval. The
      claim cannot see that, because the worker finalises before it connects,
      so ``last_sync_at`` records the sync's start and says nothing about
      whether it is still running.
    - The **CAS** covers the *same* token redelivered by Celery (worker killed
      before its ack). The lock cannot see that either: the redelivery may
      arrive long after the original released.

    Both exit before opening a connection, which is the point - the cost being
    avoided is a duplicate IMAP pass, not a duplicate row.

    ``claim_token`` is the value the dispatcher CAS-wrote into
    ``last_sync_at``. Calls without a token skip the CAS: there is no
    dispatcher window to pin against, and the caller is asserting it does not
    need the protection. They still take the lock.

    Note that the UI's per-account refresh does not come through here - it
    calls ``sync_account`` inline in the request (``MailAccountSyncView``), so
    it takes neither guard. A manual refresh can therefore overlap a
    background pass; that is pre-existing and absorbed by
    ``UniqueConstraint(folder, imap_uid)``.
    """
    from workspace.common.task_locks import task_lock
    from workspace.mail.models import MailAccount
    from workspace.mail.services.imap_sync import sync_account

    try:
        # select_related('owner'): the sync path reads account.owner to gate
        # the per-user AI classify/extract features once new messages land.
        account = MailAccount.objects.select_related("owner").get(
            uuid=account_uuid, is_active=True
        )
    except MailAccount.DoesNotExist:
        logger.warning("Account %s not found or inactive", scrub(str(account_uuid)))
        return {"status": "not_found"}

    with task_lock(f"mail:sync:account:{account.pk}", _lock_horizon_seconds()) as held:
        if not held:
            logger.info(
                "Mail sync skipped (already running): account=%s",
                scrub(str(account.pk)),
            )
            return {"status": "skipped", "reason": "already_running"}

        if claim_token and not cas_finalize(
            MailAccount,
            account.pk,
            claim_field="last_sync_at",
            claim_token=claim_token,
            updates={"last_sync_at": timezone.now()},
            extra_where={"is_active": True},
        ):
            logger.info(
                "Mail sync skipped (claimed by another worker): account=%s",
                scrub(str(account.pk)),
            )
            return {"status": "skipped", "reason": "already_claimed"}
        if claim_token:
            account.refresh_from_db(fields=["last_sync_at"])

        try:
            sync_account(account)
            return {"status": "ok", "email": account.email}
        except Exception as e:
            logger.exception("Failed to sync account %s", scrub(account.email))
            account.last_sync_error = str(e)
            account.save(update_fields=["last_sync_error", "updated_at"])
            return {"status": "error", "error": str(e)}
