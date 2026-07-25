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


def _lock_horizon():
    """How long a claim parks an account before it becomes due again.

    This is the self-healing window for a claim that is never finalised -
    the task was enqueued but no worker ever ran it (pool down, message
    dropped, queue misrouted). Until it elapses, the account does not sync.

    It only has to cover the time from enqueue to finalise (broker latency
    plus queue wait), not the sync itself, because the worker finalises
    before it opens any connection. The shared 1h default in
    ``celery_claim`` is sized for a 900s cadence; against this module's
    300s it would turn a dropped message into an hour of silence, so scale
    it to the interval instead.
    """
    return timedelta(seconds=_sync_interval() * 4)


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

    Note what the claim does and does not bound. It stops two dispatchers
    from enqueueing the same account, and it stops an account from being
    re-enqueued for one interval. It does *not* track in-flight syncs: the
    worker finalises its claim before connecting, so a pass that outlives
    ``MAIL_SYNC_INTERVAL`` can be enqueued a second time while the first is
    still running. That is wasted IMAP work rather than duplicated
    messages - ``UniqueConstraint(folder, imap_uid)`` and the IntegrityError
    guard in ``imap_sync`` already cover a concurrent sync of one folder.
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

    ``claim_token`` is the value the dispatcher CAS-wrote into
    ``last_sync_at``. The worker finalises its claim by CAS-pinning that
    exact value, so a duplicate Celery delivery whose row was already
    finalised by the winning worker matches zero rows and bails before
    opening an IMAP connection. Calls without a token skip the CAS: there
    is no dispatcher window to pin against, and the caller is asserting it
    does not need the protection.

    Note that the UI's per-account refresh does not come through here - it
    calls ``sync_account`` inline in the request (``MailAccountSyncView``).
    It still moves ``last_sync_at`` forward, so a manual sync correctly
    makes the account undue for the next interval.
    """
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
