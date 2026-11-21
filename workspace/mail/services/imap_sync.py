"""IMAP sync logic: folders, messages, reconciliation."""

import logging
from datetime import timedelta
from itertools import batched

from django.db import IntegrityError, transaction
from django.db.models import Min
from django.utils import timezone as dj_timezone
from django.utils.dateparse import parse_datetime
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from workspace.common.logging import scrub
from workspace.mail.services.imap_connection import imap_session
from workspace.mail.services.imap_mailbox import (
    _detect_folder_type,
    _display_name,
    list_folders,
)
from workspace.mail.services.imap_messages import FLAGGED, SEEN, UID_BATCH_SIZE
from workspace.mail.services.imap_parse import _parse_message

logger = logging.getLogger(__name__)

# Maximum messages to fetch on initial sync per folder
INITIAL_SYNC_LIMIT = 200
# Most messages one FETCH brings back
FETCH_BATCH_SIZE = 50
# Most bytes of message data one FETCH brings back: the whole answer is held
# in memory before it is parsed, and parsing copies each message again. A
# message larger than this is fetched on its own.
FETCH_BATCH_BYTES = 20 * 1024 * 1024
# How long a folder goes without its flags being read in full. In between a
# pass builds on what the server said last time (see `remote_state`), which
# is only right while nothing but the server changed the rows: read in full
# now and then, a folder that drifted for a reason nobody thought of comes
# back in line within the day.
FULL_READ_INTERVAL = timedelta(days=1)


def sync_folders(account):
    """Sync the list of IMAP folders for the given account to the database."""
    from ..models import MailFolder

    with imap_session(account) as client:
        remote_folders = list_folders(client)

    # Detect and store the IMAP hierarchy delimiter
    if remote_folders:
        _flags, delim, _name = remote_folders[0]
        if delim and delim != account.imap_delimiter:
            account.imap_delimiter = delim
            account.save(update_fields=["imap_delimiter", "updated_at"])

    existing = {f.name: f for f in MailFolder.objects.filter(account=account)}
    # Folders in a merge group carry the type the user chose for the group.
    # Re-detecting it from the mailbox name would silently drop a merged Sent
    # or Inbox out of the unified inbox, the activity feed and the
    # notification filter, all of which key on folder_type rather than on a
    # folder UUID.
    canonical_ids = set(
        MailFolder.objects.filter(account=account, alias_of__isnull=False).values_list(
            "alias_of_id", flat=True
        )
    )
    remote_names = set()

    with transaction.atomic():
        for flags, _delim, name in remote_folders:
            # Skip non-selectable containers (e.g. [Gmail])
            if "\\noselect" in flags or "\\nonexistent" in flags:
                continue
            # Skip \All folder (Gmail "All Mail") - duplicates every message
            if "\\all" in flags:
                continue
            remote_names.add(name)
            folder_type = _detect_folder_type(name, flags)
            display = _display_name(name)

            if name in existing:
                folder = existing[name]
                changed = False
                grouped = folder.alias_of_id is not None or folder.pk in canonical_ids
                if not grouped and folder.folder_type != folder_type:
                    folder.folder_type = folder_type
                    changed = True
                if folder.display_name != display:
                    folder.display_name = display
                    changed = True
                if changed:
                    folder.save(
                        update_fields=["folder_type", "display_name", "updated_at"]
                    )
            else:
                MailFolder.objects.create(
                    account=account,
                    name=name,
                    display_name=display,
                    folder_type=folder_type,
                )

        # Remove folders that no longer exist remotely
        gone = set(existing.keys()) - remote_names
        if gone:
            from .folder_merge import promote_alias

            gone_pks = [existing[name].pk for name in gone]
            for name in gone:
                promote_alias(existing[name], exclude_ids=gone_pks)
            MailFolder.objects.filter(account=account, name__in=gone).delete()


def remote_state(selected):
    """What a SELECT says of a mailbox, when that is enough to tell later
    that nothing in it changed. None when it is not.

    A new message moves UIDNEXT, an expunge moves EXISTS and a flag moves
    HIGHESTMODSEQ, the one counter only a CONDSTORE server (RFC 7162) keeps.
    Without it a flag set by another mail program leaves no trace here, so
    there is no state to compare and every pass reads the mailbox.
    """
    modseq = selected.get(b"HIGHESTMODSEQ")
    if not modseq:
        return None
    return {
        "uidvalidity": selected.get(b"UIDVALIDITY"),
        "uidnext": selected.get(b"UIDNEXT"),
        "exists": selected.get(b"EXISTS"),
        "highestmodseq": modseq,
    }


def _trusted_state(folder):
    """The state `folder` was left in by its last complete pass, when its
    flags were read in full recently enough to build on. None otherwise."""
    known = folder.remote_state
    if not known:
        return None
    read_at = parse_datetime(known.get("read_at") or "")
    if read_at is None or dj_timezone.now() - read_at > FULL_READ_INTERVAL:
        return None
    return known


def forget_remote_state(folder):
    """Make the next pass read `folder` in full.

    For whoever leaves a row saying something the server may not: a flag
    kept here although the server refused it is put right by the next pass,
    which has to look at the mailbox for that.
    """
    type(folder).objects.filter(pk=folder.pk).update(remote_state=None)
    folder.remote_state = None


def sync_folder_messages(account, folder):
    """Incrementally sync messages for one folder."""
    with imap_session(account) as client:
        try:
            selected = client.select_folder(folder.name, readonly=True)
        except IMAPClientAbortError:
            raise
        except IMAPClientError as e:
            logger.warning(
                "Could not SELECT folder %s: %s", scrub(folder.name), scrub(e)
            )
            return

        state = remote_state(selected)
        known = _trusted_state(folder)
        if state is not None and known is not None and state.items() <= known.items():
            # Nothing arrived, left or changed flags since the last pass.
            return

        was_initial_sync, new_message_uuids, error = _read_mailbox(
            client, account, folder, selected, state, known
        )
        _after_import(account, folder, new_message_uuids, was_initial_sync)
        if error is not None:
            raise error


def _read_mailbox(client, account, folder, selected, state, known):
    """Bring `folder` in line with the mailbox `client` has selected.

    `state` is what the SELECT said of the mailbox and `known` what the last
    pass left, each None when there is nothing to go by.

    Returns ``(was_initial_sync, new_message_uuids, error)``. The last one
    is what cut the pass short, if anything did: the caller raises it once
    the messages imported before it have had their rules and notifications.
    Raised from here, it would cost them both for good, since the next pass
    finds those messages stored and has no reason to look at them again.
    """
    from ..models import MailMessage

    known_modseq = None
    uid_validity = selected.get(b"UIDVALIDITY")
    if uid_validity and folder.uid_validity and folder.uid_validity != uid_validity:
        # The cursor counts UIDs of the old validity: stored apart from the
        # new validity, a sync dying in between would skip every message
        # whose new UID falls at or below it, for good.
        logger.info("UIDVALIDITY changed for %s, resetting", scrub(folder.name))
        with transaction.atomic():
            MailMessage.objects.filter(folder=folder).delete()
            folder.uid_validity = uid_validity
            folder.last_sync_uid = 0
            folder.remote_state = None
            folder.save(
                update_fields=[
                    "uid_validity",
                    "last_sync_uid",
                    "remote_state",
                    "updated_at",
                ]
            )
    else:
        if uid_validity and folder.uid_validity != uid_validity:
            folder.uid_validity = uid_validity
            folder.save(update_fields=["uid_validity", "updated_at"])
        if state is not None and known is not None:
            known_modseq = known.get("highestmodseq")

    # Captured here on purpose. Reading it at function entry would miss the
    # UIDVALIDITY reset just above, which purges the folder and re-imports
    # everything - as silent an event as a first sync. Reading it at the end
    # would always say "not initial", because the cursor advances below.
    was_initial_sync = folder.last_sync_uid == 0

    new_message_uuids = []
    error = None
    try:
        if was_initial_sync:
            remote_uids = sorted(client.search("ALL"))
            new_uids = remote_uids[-INITIAL_SYNC_LIMIT:]
        else:
            # One SEARCH for both questions: which messages are new, and
            # which of the stored ones are still there. Its command line is
            # the same length whatever the size of the folder.
            oldest = MailMessage.objects.filter(
                folder=folder, deleted_at__isnull=True
            ).aggregate(oldest=Min("imap_uid"))["oldest"]
            first_new = folder.last_sync_uid + 1
            remote_uids = sorted(
                client.search(["UID", f"{min(oldest or first_new, first_new)}:*"])
            )
            new_uids = [uid for uid in remote_uids if uid > folder.last_sync_uid]

        for batch in _fetch_batches(client, new_uids):
            new_message_uuids += _import_batch(client, account, folder, batch)
            # Saved batch by batch: a pass that dies further on (a worker
            # killed for its memory, a connection lost) resumes here instead
            # of downloading everything again. Not on a first pass, which
            # starts over: resumed, it would no longer be a first pass and
            # would notify for mail that is weeks old.
            if not was_initial_sync:
                folder.save(update_fields=["last_sync_uid", "updated_at"])

        # Reconciliation: detect messages deleted/moved by other clients
        _reconcile_folder(client, folder, remote_uids, changed_since=known_modseq)
    except Exception as e:
        logger.exception("Sync of %s cut short", scrub(folder.name))
        error = e
        if was_initial_sync:
            folder.last_sync_uid = 0

    _update_folder_counts(folder)

    if error is None:
        new_state = None
        if state is not None:
            # CHANGEDSINCE read part of the flags: the last full read stays
            # the one it was.
            read_at = known["read_at"] if known_modseq else dj_timezone.now()
            new_state = {**state, "read_at": str(read_at)}
        if new_state != folder.remote_state:
            folder.remote_state = new_state
            folder.save(update_fields=["remote_state", "updated_at"])

    return was_initial_sync, new_message_uuids, error


def _import_batch(client, account, folder, batch):
    """Fetch and store the messages `batch` names, moving the folder's
    cursor (unsaved) past the last one stored. Returns their UUIDs."""
    from ..models import MailMessage

    fetched = client.fetch(batch, ["FLAGS", "BODY.PEEK[]"])

    # One existence probe for the whole batch; _parse_message would
    # otherwise check each message individually (N queries per
    # batch, paid in full on initial sync and crash re-sync).
    known_uids = set(
        MailMessage.objects.filter(folder=folder, imap_uid__in=batch).values_list(
            "imap_uid", flat=True
        )
    )

    new_message_uuids = []
    max_uid = folder.last_sync_uid
    for uid in batch:
        data = fetched.get(uid)
        if data is None or data.get(b"BODY[]") is None:
            # Expunged between the SEARCH and the FETCH.
            continue
        try:
            msg = _parse_message(
                data[b"BODY[]"],
                account,
                folder,
                uid,
                _flags(data),
                known_uids=known_uids,
            )
            # Advance max_uid even when msg is None (already present in
            # DB): otherwise last_sync_uid never moves past UIDs we've
            # confirmed, and every future sync re-FETCHes the same
            # messages - happens after a crash mid-sync where some
            # messages were persisted but last_sync_uid wasn't updated.
            max_uid = max(max_uid, uid)
            if msg:
                new_message_uuids.append(str(msg.uuid))
        except IntegrityError:
            # Duplicate-insert race with a concurrent sync of this
            # folder: the batch existence snapshot above predates
            # the other sync's insert, so the (folder, imap_uid)
            # unique constraint fires. The message is stored, just
            # not by us - skip it and keep the cursor moving.
            if MailMessage.objects.filter(folder=folder, imap_uid=uid).exists():
                max_uid = max(max_uid, uid)
            else:
                logger.exception(
                    "Failed to parse message UID %d in %s",
                    uid,
                    scrub(folder.name),
                )
        except Exception:
            logger.exception(
                "Failed to parse message UID %d in %s", uid, scrub(folder.name)
            )

    folder.last_sync_uid = max(folder.last_sync_uid, max_uid)
    return new_message_uuids


def _flags(fetched):
    """The flags of one FETCH answer, as text."""
    return frozenset(
        flag.decode("ascii", errors="replace") if isinstance(flag, bytes) else str(flag)
        for flag in fetched.get(b"FLAGS") or ()
    )


def _fetch_batches(client, uids):
    """Cut `uids` into the groups one FETCH brings back whole.

    At most FETCH_BATCH_SIZE messages and FETCH_BATCH_BYTES of message
    data: fifty scans of 20 MB in one answer would put a gigabyte in the
    worker. A message larger than the budget makes a group of its own.
    """
    if len(uids) <= 1:
        # Nothing to group: not worth asking for a size.
        if uids:
            yield list(uids)
        return

    sizes = {}
    for chunk in batched(uids, UID_BATCH_SIZE, strict=False):
        for uid, data in client.fetch(chunk, ["RFC822.SIZE"]).items():
            sizes[uid] = data.get(b"RFC822.SIZE") or 0

    batch, weight = [], 0
    for uid in uids:
        if uid not in sizes:
            # Expunged since the SEARCH.
            continue
        if batch and (
            len(batch) == FETCH_BATCH_SIZE or weight + sizes[uid] > FETCH_BATCH_BYTES
        ):
            yield batch
            batch, weight = [], 0
        batch.append(uid)
        weight += sizes[uid]
    if batch:
        yield batch


def _after_import(account, folder, new_message_uuids, was_initial_sync):
    """Hand the messages a pass just imported to rules, notifications, AI
    and the calendar."""
    # Run user-defined rules first - explicit user intent takes precedence
    # over AI classification. Wrapped in try/except so a misbehaving rule
    # never breaks the sync.
    if new_message_uuids and folder.folder_type not in ("sent", "drafts"):
        try:
            from workspace.mail.services.rules.engine import run_rules_for_messages

            run_rules_for_messages(account, new_message_uuids)
        except Exception:
            logger.exception("rules engine failed for %s", scrub(folder.name))

    # After rules, not before: notify_new_messages re-queries is_read and
    # deleted_at, so a rule that marks read/deletes a message here also
    # suppresses its push. Own try/except, like the AI dispatch below: a
    # notification failure must never break a sync.
    try:
        from workspace.mail.services.notifications import notify_new_messages

        notify_new_messages(
            folder, new_message_uuids, was_initial_sync=was_initial_sync
        )
    except Exception:
        logger.exception("Failed to send mail notifications for %s", scrub(folder.name))

    # Dispatch AI classification / extraction for new messages
    # (skip sent/drafts). Classify and extract are gated by independent
    # per-user toggles AND wrapped in independent try/except blocks so a
    # failure dispatching one never prevents the other.
    if new_message_uuids and folder.folder_type not in ("sent", "drafts"):
        try:
            from workspace.ai.client import is_ai_enabled
            from workspace.mail.services.ai_settings import (
                is_mail_ai_feature_enabled,
            )

            if is_ai_enabled():
                from workspace.ai.models import AITask
                from workspace.ai.services.dispatch import dispatch

                dispatched = []
                if (
                    is_mail_ai_feature_enabled(account.owner, "classify")
                    and not folder.ai_classify_disabled
                ):
                    try:
                        dispatch(
                            owner=account.owner,
                            task_type=AITask.TaskType.CLASSIFY,
                            input_data={
                                "message_uuids": new_message_uuids,
                                "suppress_notifications": was_initial_sync,
                            },
                        )
                        dispatched.append("classify")
                    except Exception:
                        logger.exception(
                            "Failed to dispatch classify task for %s",
                            scrub(folder.name),
                        )
                if is_mail_ai_feature_enabled(account.owner, "extract"):
                    try:
                        dispatch(
                            owner=account.owner,
                            task_type=AITask.TaskType.EXTRACT,
                            input_data={"message_uuids": new_message_uuids},
                        )
                        dispatched.append("extract")
                    except Exception:
                        logger.exception(
                            "Failed to dispatch extract task for %s",
                            scrub(folder.name),
                        )
                if dispatched:
                    logger.info(
                        "Dispatched %s tasks for %d new messages in %s",
                        "+".join(dispatched),
                        len(new_message_uuids),
                        scrub(folder.name),
                    )
        except Exception:
            logger.exception(
                "Failed to dispatch classify/extract tasks for %s",
                scrub(folder.name),
            )

    # Process calendar invitations among messages just synced.
    # Scoping on new_message_uuids avoids re-parsing every old ICS
    # email on every sync (has_calendar_event is set once at parse
    # time and never flipped later, so old messages never need a
    # second pass).
    if new_message_uuids:
        from ..models import MailMessage as _MailMsg

        cal_messages = _MailMsg.objects.filter(
            folder=folder,
            has_calendar_event=True,
            uuid__in=new_message_uuids,
        )
        if cal_messages.exists():
            from workspace.calendar.services.ics_processor import (
                process_calendar_emails,
            )

            process_calendar_emails(cal_messages)


def _reconcile_folder(client, folder, remote_uids, changed_since=None):
    """Remove local messages whose UIDs no longer exist on the IMAP server.

    Also updates flags (read/starred) for messages that still exist.

    `remote_uids` are the UIDs the server holds, at least from the oldest
    stored one up. `changed_since` is the HIGHESTMODSEQ the folder had when
    its flags were last read in full: with it the server only answers for
    the messages whose flags moved since (CONDSTORE), and the others keep
    the flags they have here.
    """
    from ..models import MailMessage

    local_msgs = MailMessage.objects.filter(
        folder=folder,
        deleted_at__isnull=True,
    ).values_list("imap_uid", flat=True)
    local_uids = set(local_msgs)
    if not local_uids:
        return
    remote_uids = set(remote_uids)

    # Soft-delete messages that are no longer on the server
    gone = local_uids - remote_uids
    if gone:
        # Captured before the update: the row survives (this is a soft
        # delete, not a CASCADE), but pk is what the notification helper
        # needs, and gone/imap_uid alone can't join back to it after.
        gone_pks = list(
            MailMessage.objects.filter(
                folder=folder,
                imap_uid__in=gone,
                deleted_at__isnull=True,
            ).values_list("pk", flat=True)
        )
        count = MailMessage.objects.filter(
            pk__in=gone_pks, deleted_at__isnull=True
        ).update(deleted_at=dj_timezone.now())
        if count:
            logger.info(
                "Reconciled %s: soft-deleted %d messages no longer on server",
                scrub(folder.name),
                count,
            )
            from workspace.mail.services.notifications import (
                settle_message_notifications,
            )

            settle_message_notifications(folder.account.owner, gone_pks)

    # Update flags for remaining messages
    present = local_uids & remote_uids
    if not present:
        return

    # In commands of bounded length: one naming every UID of a large folder
    # is longer than a server accepts.
    modifiers = [f"CHANGEDSINCE {changed_since}"] if changed_since else None
    remote_flags = {}
    for batch in batched(sorted(present), UID_BATCH_SIZE, strict=False):
        for uid, data in client.fetch(batch, ["FLAGS"], modifiers).items():
            if b"FLAGS" in data:
                remote_flags[uid] = _flags(data)
    if not remote_flags:
        return

    # Load current local state to diff against remote
    local_state = MailMessage.objects.filter(
        folder=folder,
        imap_uid__in=remote_flags,
        deleted_at__isnull=True,
    ).values_list("imap_uid", "is_read", "is_starred")

    need_read = set()
    need_unread = set()
    need_starred = set()
    need_unstarred = set()
    for uid, is_read, is_starred in local_state:
        should_read = SEEN in remote_flags[uid]
        should_star = FLAGGED in remote_flags[uid]
        if should_read and not is_read:
            need_read.add(uid)
        elif not should_read and is_read:
            need_unread.add(uid)
        if should_star and not is_starred:
            need_starred.add(uid)
        elif not should_star and is_starred:
            need_unstarred.add(uid)

    base = MailMessage.objects.filter(folder=folder, deleted_at__isnull=True)

    # Capture pks for messages whose read state changes BEFORE running the
    # update, so we can refresh the denormalized MailLabel.unread_count after.
    # Star changes don't affect unread_count, so we don't track them here.
    read_changed_uids = need_read | need_unread
    affected_message_ids = []
    if read_changed_uids:
        affected_message_ids = list(
            base.filter(imap_uid__in=read_changed_uids).values_list("pk", flat=True)
        )

    externally_read_pks = []
    if need_read:
        externally_read_pks = list(
            base.filter(imap_uid__in=need_read).values_list("pk", flat=True)
        )
        base.filter(imap_uid__in=need_read).update(is_read=True)
    if need_unread:
        base.filter(imap_uid__in=need_unread).update(is_read=False)
    if need_starred:
        base.filter(imap_uid__in=need_starred).update(is_starred=True)
    if need_unstarred:
        base.filter(imap_uid__in=need_unstarred).update(is_starred=False)

    if affected_message_ids:
        from .label_counts import refresh_labels_for_messages

        refresh_labels_for_messages(affected_message_ids)

    # A message read from another client was seen there; its notification
    # would otherwise stay unread forever (no page render happens here).
    if externally_read_pks:
        from .notifications import settle_message_notifications

        settle_message_notifications(folder.account.owner, externally_read_pks)


def _update_folder_counts(folder):
    """Update message_count and unread_count from database."""
    from django.db.models import Count, Q

    from ..models import MailMessage

    counts = MailMessage.objects.filter(
        folder=folder,
        deleted_at__isnull=True,
    ).aggregate(
        message_count=Count("pk"),
        unread_count=Count("pk", filter=Q(is_read=False)),
    )
    folder.message_count = counts["message_count"]
    folder.unread_count = counts["unread_count"]
    folder.save(
        update_fields=["message_count", "unread_count", "last_sync_uid", "updated_at"]
    )


def sync_account(account):
    """Full sync: folders then messages for each folder, on one connection."""
    from ..models import MailFolder

    with imap_session(account):
        sync_folders(account)
        error_occurred = False
        for folder in MailFolder.objects.filter(account=account):
            try:
                sync_folder_messages(account, folder)
            except Exception:
                logger.exception(
                    "Failed to sync folder %s for %s",
                    scrub(folder.name),
                    scrub(account.email),
                )
                error_occurred = True

    account.last_sync_at = dj_timezone.now()
    account.last_sync_error = "Some folders failed to sync." if error_occurred else ""
    account.save(update_fields=["last_sync_at", "last_sync_error", "updated_at"])


def accounts_with_sync_errors():
    """Active accounts whose last sync recorded an error."""
    from workspace.mail.models import MailAccount

    return MailAccount.objects.filter(is_active=True).exclude(last_sync_error="")


def queue_account_syncs(accounts):
    """Queue a background sync for the active accounts in *accounts*.

    Returns the number of syncs queued. Manual dispatches carry no claim
    token: there is no dispatcher window to pin against (see
    ``mail.sync_account``).
    """
    from workspace.mail.tasks import sync_single_account

    count = 0
    for account in accounts.filter(is_active=True):
        sync_single_account.delay(str(account.uuid))
        count += 1
    return count
