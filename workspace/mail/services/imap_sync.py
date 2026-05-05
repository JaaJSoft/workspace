"""IMAP sync logic: folders, messages, reconciliation."""

import logging
import re

from django.db import transaction
from django.utils import timezone as dj_timezone

from workspace.mail.services.imap_connection import connect_imap
from workspace.mail.services.imap_mailbox import (
    _detect_folder_type,
    _display_name,
    _quote_mailbox,
    list_folders,
)
from workspace.mail.services.imap_parse import _parse_message

logger = logging.getLogger(__name__)

# Maximum messages to fetch on initial sync per folder
INITIAL_SYNC_LIMIT = 200
# Batch size for FETCH commands
FETCH_BATCH_SIZE = 50


def sync_folders(account):
    """Sync the list of IMAP folders for the given account to the database."""
    from ..models import MailFolder

    conn = connect_imap(account)
    try:
        remote_folders = list_folders(conn)
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass

    # Detect and store the IMAP hierarchy delimiter
    if remote_folders:
        _flags, delim, _name = remote_folders[0]
        if delim and delim != account.imap_delimiter:
            account.imap_delimiter = delim
            account.save(update_fields=['imap_delimiter', 'updated_at'])

    existing = {f.name: f for f in MailFolder.objects.filter(account=account)}
    remote_names = set()

    with transaction.atomic():
        for flags, _delim, name in remote_folders:
            # Skip non-selectable containers (e.g. [Gmail])
            if '\\noselect' in flags.lower():
                continue
            # Skip \All folder (Gmail "All Mail") - duplicates every message
            if '\\all' in flags.lower():
                continue
            remote_names.add(name)
            folder_type = _detect_folder_type(name, flags)
            display = _display_name(name)

            if name in existing:
                folder = existing[name]
                changed = False
                if folder.folder_type != folder_type:
                    folder.folder_type = folder_type
                    changed = True
                if folder.display_name != display:
                    folder.display_name = display
                    changed = True
                if changed:
                    folder.save(update_fields=['folder_type', 'display_name', 'updated_at'])
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
            MailFolder.objects.filter(account=account, name__in=gone).delete()


def _get_uidvalidity(conn):
    """Extract UIDVALIDITY from the last SELECT/EXAMINE response."""
    for resp in (conn.untagged_responses.get('OK') or []):
        decoded = resp.decode('ascii', errors='replace') if isinstance(resp, bytes) else resp
        m = re.search(r'\[UIDVALIDITY\s+(\d+)\]', decoded)
        if m:
            return int(m.group(1))
    return None


def sync_folder_messages(account, folder):
    """Incrementally sync messages for one folder."""
    from ..models import MailMessage

    conn = connect_imap(account)
    try:
        status, data = conn.select(_quote_mailbox(folder.name), readonly=True)
        if status != 'OK':
            logger.warning("Could not SELECT folder %s: %s", folder.name, data)
            return

        # Check UIDVALIDITY (parsed from untagged OK response, NOT from
        # data[0] which is the EXISTS message count)
        uid_validity = _get_uidvalidity(conn)
        if uid_validity and folder.uid_validity and folder.uid_validity != uid_validity:
            # UIDVALIDITY changed - purge and re-sync
            logger.info("UIDVALIDITY changed for %s, resetting", folder.name)
            MailMessage.objects.filter(folder=folder).delete()
            folder.last_sync_uid = 0

        if uid_validity:
            folder.uid_validity = uid_validity
        folder.save(update_fields=['uid_validity', 'updated_at'])

        # Search for new UIDs (always use UID SEARCH to get real UIDs)
        uid_list = []
        if folder.last_sync_uid > 0:
            status, search_data = conn.uid('SEARCH', None, f'UID {folder.last_sync_uid + 1}:*')
            if status == 'OK':
                uid_list = search_data[0].split()
                uid_list = [u.decode() if isinstance(u, bytes) else u for u in uid_list if u]
                # Filter out the already-synced UID (server may include it)
                uid_list = [u for u in uid_list if int(u) > folder.last_sync_uid]
        else:
            # Initial sync: get all UIDs then take last N
            status, search_data = conn.uid('SEARCH', None, 'ALL')
            if status == 'OK':
                all_uids = search_data[0].split()
                all_uids = [u.decode() if isinstance(u, bytes) else u for u in all_uids if u]
                # Limit initial sync
                if len(all_uids) > INITIAL_SYNC_LIMIT:
                    all_uids = all_uids[-INITIAL_SYNC_LIMIT:]
                uid_list = all_uids

        max_uid = folder.last_sync_uid
        new_message_uuids = []
        # Fetch in batches
        for i in range(0, len(uid_list), FETCH_BATCH_SIZE):
            batch = uid_list[i:i + FETCH_BATCH_SIZE]
            uid_set = ','.join(batch)
            status, msg_data = conn.uid('FETCH', uid_set, '(UID FLAGS RFC822)')
            if status != 'OK':
                continue

            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue
                # Parse UID from response
                uid_match = re.search(rb'UID (\d+)', response_part[0])
                if not uid_match:
                    continue
                uid = int(uid_match.group(1))

                # Parse flags
                flags_match = re.search(rb'FLAGS \(([^)]*)\)', response_part[0])
                flags_str = flags_match.group(1).decode() if flags_match else ''

                raw_email = response_part[1]
                try:
                    msg = _parse_message(raw_email, account, folder, uid, flags_str)
                    if msg:
                        max_uid = max(max_uid, uid)
                        new_message_uuids.append(str(msg.uuid))
                except Exception:
                    logger.exception("Failed to parse message UID %d in %s", uid, folder.name)

        # Update sync position
        if max_uid > folder.last_sync_uid:
            folder.last_sync_uid = max_uid

        # Reconciliation: detect messages deleted/moved by other clients
        _reconcile_folder(conn, folder)

        _update_folder_counts(folder)

        # Dispatch AI classification for new messages (skip sent/drafts)
        if new_message_uuids and folder.folder_type not in ('sent', 'drafts'):
            try:
                from workspace.ai.client import is_ai_enabled
                from workspace.users.services.settings import get_setting
                if is_ai_enabled() and get_setting(account.owner, 'mail', 'ai_enabled', default=True):
                    from workspace.ai.models import AITask
                    ai_task = AITask.objects.create(
                        owner=account.owner,
                        task_type=AITask.TaskType.CLASSIFY,
                        input_data={'message_uuids': new_message_uuids},
                    )
                    from workspace.ai.tasks import classify_mail_messages
                    classify_mail_messages.delay(str(ai_task.uuid))
                    logger.info('Dispatched classify task for %d new messages in %s',
                                len(new_message_uuids), folder.name)
            except Exception:
                logger.exception('Failed to dispatch classify task for %s', folder.name)

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
                from workspace.calendar.services.ics_processor import process_calendar_emails
                process_calendar_emails(cal_messages)

    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass


def _reconcile_folder(conn, folder):
    """Remove local messages whose UIDs no longer exist on the IMAP server.

    Also updates flags (read/starred) for messages that still exist.
    """
    from ..models import MailMessage

    local_msgs = MailMessage.objects.filter(
        folder=folder, deleted_at__isnull=True,
    ).values_list('imap_uid', flat=True)
    local_uids = set(local_msgs)
    if not local_uids:
        return

    # Ask the server for all UIDs currently in this folder
    status, search_data = conn.uid('SEARCH', None, 'ALL')
    if status != 'OK':
        return
    raw = search_data[0].split() if search_data[0] else []
    remote_uids = {int(u.decode() if isinstance(u, bytes) else u) for u in raw if u}

    # Soft-delete messages that are no longer on the server
    gone = local_uids - remote_uids
    if gone:
        count = MailMessage.objects.filter(
            folder=folder, imap_uid__in=gone, deleted_at__isnull=True,
        ).update(deleted_at=dj_timezone.now())
        if count:
            logger.info('Reconciled %s: soft-deleted %d messages no longer on server', folder.name, count)

    # Update flags for remaining messages
    present = local_uids & remote_uids
    if not present:
        return
    uid_set = ','.join(str(u) for u in sorted(present))
    status, flags_data = conn.uid('FETCH', uid_set, '(UID FLAGS)')
    if status != 'OK':
        return

    remote_read = set()
    remote_starred = set()
    for response_part in flags_data:
        if not isinstance(response_part, (tuple, bytes)):
            continue
        raw_line = response_part[0] if isinstance(response_part, tuple) else response_part
        if not isinstance(raw_line, bytes):
            continue
        uid_match = re.search(rb'UID (\d+)', raw_line)
        flags_match = re.search(rb'FLAGS \(([^)]*)\)', raw_line)
        if not uid_match:
            continue
        uid = int(uid_match.group(1))
        flags_str = flags_match.group(1).decode() if flags_match else ''
        if r'\Seen' in flags_str:
            remote_read.add(uid)
        if r'\Flagged' in flags_str:
            remote_starred.add(uid)

    # Load current local state to diff against remote
    local_state = MailMessage.objects.filter(
        folder=folder, imap_uid__in=present, deleted_at__isnull=True,
    ).values_list('imap_uid', 'is_read', 'is_starred')

    need_read = set()
    need_unread = set()
    need_starred = set()
    need_unstarred = set()
    for uid, is_read, is_starred in local_state:
        should_read = uid in remote_read
        should_star = uid in remote_starred
        if should_read and not is_read:
            need_read.add(uid)
        elif not should_read and is_read:
            need_unread.add(uid)
        if should_star and not is_starred:
            need_starred.add(uid)
        elif not should_star and is_starred:
            need_unstarred.add(uid)

    base = MailMessage.objects.filter(folder=folder, deleted_at__isnull=True)
    if need_read:
        base.filter(imap_uid__in=need_read).update(is_read=True)
    if need_unread:
        base.filter(imap_uid__in=need_unread).update(is_read=False)
    if need_starred:
        base.filter(imap_uid__in=need_starred).update(is_starred=True)
    if need_unstarred:
        base.filter(imap_uid__in=need_unstarred).update(is_starred=False)


def _update_folder_counts(folder):
    """Update message_count and unread_count from database."""
    from django.db.models import Count, Q
    from ..models import MailMessage

    counts = MailMessage.objects.filter(
        folder=folder, deleted_at__isnull=True,
    ).aggregate(
        message_count=Count('pk'),
        unread_count=Count('pk', filter=Q(is_read=False)),
    )
    folder.message_count = counts['message_count']
    folder.unread_count = counts['unread_count']
    folder.save(update_fields=['message_count', 'unread_count', 'last_sync_uid', 'updated_at'])


def sync_account(account):
    """Full sync: folders then messages for each folder."""
    from ..models import MailFolder

    sync_folders(account)
    error_occurred = False
    for folder in MailFolder.objects.filter(account=account):
        try:
            sync_folder_messages(account, folder)
        except Exception:
            logger.exception("Failed to sync folder %s for %s", folder.name, account.email)
            error_occurred = True

    account.last_sync_at = dj_timezone.now()
    account.last_sync_error = 'Some folders failed to sync.' if error_occurred else ''
    account.save(update_fields=['last_sync_at', 'last_sync_error', 'updated_at'])
