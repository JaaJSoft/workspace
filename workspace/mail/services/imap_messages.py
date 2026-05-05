"""IMAP per-message operations: flags, delete, move, drafts, sent."""

import imaplib
import logging
import time

from django.utils import timezone as dj_timezone

from workspace.mail.services.imap_connection import connect_imap
from workspace.mail.services.imap_mailbox import _quote_mailbox

logger = logging.getLogger(__name__)


def mark_read(account, message):
    """Mark a message as read on the IMAP server."""
    _set_flag(account, message, '\\Seen', True)


def mark_unread(account, message):
    """Mark a message as unread on the IMAP server."""
    _set_flag(account, message, '\\Seen', False)


def star_message(account, message):
    """Star a message on the IMAP server."""
    _set_flag(account, message, '\\Flagged', True)


def unstar_message(account, message):
    """Unstar a message on the IMAP server."""
    _set_flag(account, message, '\\Flagged', False)


def delete_message(account, message):
    """Mark a message as deleted on the IMAP server, then expunge."""
    conn = connect_imap(account)
    try:
        conn.select(_quote_mailbox(message.folder.name))
        conn.uid('STORE', str(message.imap_uid), '+FLAGS', '(\\Deleted)')
        conn.expunge()
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass


def move_message(account, message, target_folder):
    """Move a message to another folder via IMAP COPY + DELETE."""
    conn = connect_imap(account)
    try:
        conn.select(_quote_mailbox(message.folder.name))
        # imaplib does NOT raise on a 'NO' response - it returns (status, data).
        # If COPY fails (target gone, quota exceeded, perms denied) and we
        # don't check, the STORE+EXPUNGE below would permanently delete the
        # source message with no copy in target: irrecoverable data loss.
        st, data = conn.uid('COPY', str(message.imap_uid), _quote_mailbox(target_folder.name))
        if st != 'OK':
            raise Exception(f'IMAP COPY failed: {data}')
        conn.uid('STORE', str(message.imap_uid), '+FLAGS', '(\\Deleted)')
        conn.expunge()
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass


def _set_flag(account, message, flag, add):
    """Add or remove an IMAP flag on a message."""
    conn = connect_imap(account)
    try:
        conn.select(_quote_mailbox(message.folder.name))
        op = '+FLAGS' if add else '-FLAGS'
        conn.uid('STORE', str(message.imap_uid), op, f'({flag})')
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass


def append_to_sent(account, raw_message_bytes):
    """Append a sent message to the account's Sent folder via IMAP APPEND.

    Checks first whether the server already auto-copied the message
    (Gmail, Outlook, etc.) by searching for its Message-ID to avoid duplicates.
    """
    from ..models import MailFolder

    sent_folder = (
        MailFolder.objects
        .filter(account=account, folder_type=MailFolder.FolderType.SENT)
        .first()
    )
    if not sent_folder:
        logger.warning("No Sent folder found for %s, skipping APPEND", account.email)
        return

    # Extract Message-ID from raw bytes to check for duplicates
    msg_id = None
    for line in raw_message_bytes.split(b'\n'):
        if line.lower().startswith(b'message-id:'):
            msg_id = line.split(b':', 1)[1].strip().decode(errors='replace')
            break

    conn = connect_imap(account)
    try:
        conn.select(_quote_mailbox(sent_folder.name), readonly=True)

        # Check if the server already auto-copied it
        if msg_id:
            status, data = conn.uid('SEARCH', None, f'HEADER Message-ID "{msg_id}"')
            if status == 'OK' and data[0] and data[0].strip():
                logger.info("Message already in Sent for %s (auto-copied by server)", account.email)
                return

        # Not found - append it ourselves
        conn.select(_quote_mailbox(sent_folder.name), readonly=False)
        status, _ = conn.append(
            _quote_mailbox(sent_folder.name),
            '(\\Seen)',
            imaplib.Time2Internaldate(time.time()),
            raw_message_bytes,
        )
        if status == 'OK':
            logger.info("Appended sent message to %s for %s", sent_folder.name, account.email)
        else:
            logger.warning("IMAP APPEND to %s failed for %s", sent_folder.name, account.email)
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass


def save_draft(account, raw_message_bytes, old_uid=None):
    """Save a draft message to the account's Drafts folder via IMAP APPEND.

    If old_uid is provided, deletes the previous draft first.
    Returns the created MailMessage.
    """
    from ..models import MailFolder
    from workspace.mail.services.imap_sync import sync_folder_messages

    drafts_folder = (
        MailFolder.objects
        .filter(account=account, folder_type=MailFolder.FolderType.DRAFTS)
        .first()
    )
    if not drafts_folder:
        logger.warning("No Drafts folder found for %s, skipping save_draft", account.email)
        return None

    # Extract Message-ID from raw bytes BEFORE the network call so we can find
    # our exact draft after sync. order_by('-created_at') is unsafe: a parallel
    # IMAP session (mobile, web client) could APPEND a different draft between
    # our APPEND and our sync, and we'd return the wrong message.
    msg_id = None
    for line in raw_message_bytes.split(b'\n'):
        if line.lower().startswith(b'message-id:'):
            msg_id = line.split(b':', 1)[1].strip().decode(errors='replace')
            break

    conn = connect_imap(account)
    try:
        conn.select(_quote_mailbox(drafts_folder.name), readonly=False)

        # Append new draft FIRST, then delete the old one only on APPEND
        # success. The reverse order risks losing both copies if APPEND fails
        # (network drop, quota exceeded), since EXPUNGE is irreversible.
        status, _ = conn.append(
            _quote_mailbox(drafts_folder.name),
            '(\\Draft \\Seen)',
            imaplib.Time2Internaldate(time.time()),
            raw_message_bytes,
        )
        if status != 'OK':
            logger.warning("IMAP APPEND draft to %s failed for %s", drafts_folder.name, account.email)
            return None

        if old_uid:
            conn.uid('STORE', str(old_uid), '+FLAGS', '(\\Deleted)')
            conn.expunge()

        logger.info("Saved draft to %s for %s", drafts_folder.name, account.email)
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass

    # Sync to pick up the new message locally
    sync_folder_messages(account, drafts_folder)

    from ..models import MailMessage
    qs = MailMessage.objects.filter(folder=drafts_folder, deleted_at__isnull=True)
    if msg_id:
        # Identify the exact draft we just appended.
        return qs.filter(message_id=msg_id).first()
    # Fallback: a draft built without a Message-ID header is anomalous, but
    # don't regress vs. the previous behavior in that case.
    return qs.order_by('-created_at').first()


def delete_draft(account, message):
    """Delete a draft message from the IMAP server and locally."""
    conn = connect_imap(account)
    try:
        conn.select(_quote_mailbox(message.folder.name), readonly=False)
        conn.uid('STORE', str(message.imap_uid), '+FLAGS', '(\\Deleted)')
        conn.expunge()
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass

    message.deleted_at = dj_timezone.now()
    message.save(update_fields=['deleted_at', 'updated_at'])
