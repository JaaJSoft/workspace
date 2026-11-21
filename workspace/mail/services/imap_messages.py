"""IMAP per-message operations: flags, delete, move, drafts, sent."""

import logging
from email import policy
from email.parser import BytesHeaderParser
from itertools import batched

from django.utils import timezone as dj_timezone
from imapclient.exceptions import IMAPClientError
from imapclient.imapclient import literal

from workspace.common.logging import scrub
from workspace.mail.services.imap_connection import (
    ConnectionLost,
    append_file,
    imap_session,
    last_response_code,
)

logger = logging.getLogger(__name__)

# UIDs per command: keeps each command line well under the length servers
# accept (Dovecot and Cyrus cut off around 64 KB).
UID_BATCH_SIZE = 500

SEEN = "\\Seen"
FLAGGED = "\\Flagged"
ANSWERED = "\\Answered"
DELETED = "\\Deleted"
DRAFT = "\\Draft"


def mark_read(account, message):
    """Mark a message as read on the IMAP server."""
    _set_flag(account, message, SEEN, True)


def mark_unread(account, message):
    """Mark a message as unread on the IMAP server."""
    _set_flag(account, message, SEEN, False)


def star_message(account, message):
    """Star a message on the IMAP server."""
    _set_flag(account, message, FLAGGED, True)


def unstar_message(account, message):
    """Unstar a message on the IMAP server."""
    _set_flag(account, message, FLAGGED, False)


def mark_answered(account, message):
    """Mark a message as answered on the IMAP server."""
    _set_flag(account, message, ANSWERED, True)


def _set_flag(account, message, flag, add):
    """Add or remove an IMAP flag on a message.

    Raises when the server refuses the SELECT or the STORE: a caller told
    nothing would believe the flag reached the server. One that keeps the
    flag on its row all the same owes the sync a `forget_remote_state`.
    """
    with imap_session(account) as client:
        client.select_folder(message.folder.name)
        if add:
            client.add_flags([message.imap_uid], [flag], silent=True)
        else:
            client.remove_flags([message.imap_uid], [flag], silent=True)


def mark_uids_read(account, folder, uids):
    """Set ``\\Seen`` on the messages of `folder` carrying these IMAP UIDs.

    Raises when the server refuses the SELECT or a STORE, with the same
    debt to the sync as `_set_flag`.
    """
    with imap_session(account) as client:
        client.select_folder(folder.name)
        for batch in batched(sorted(uids), UID_BATCH_SIZE, strict=False):
            client.add_flags(batch, [SEEN], silent=True)


def fetch_original(account, message, headers_only=False):
    """The message as the server stores it: every byte, or its header block.

    Neither is kept locally - the sync stores what it parsed out - so this
    asks the server each time. PEEK, so reading it leaves the message unread.
    Raises LookupError when the server no longer holds the UID.
    """
    section = "BODY.PEEK[HEADER]" if headers_only else "BODY.PEEK[]"
    with imap_session(account) as client:
        client.select_folder(message.folder.name, readonly=True)
        item = client.fetch([message.imap_uid], [section]).get(message.imap_uid)
    key = b"BODY[HEADER]" if headers_only else b"BODY[]"
    if not item or item.get(key) is None:
        raise LookupError(message.imap_uid)
    return bytes(item[key])


def delete_message(account, message):
    """Remove a message from the IMAP server for good."""
    with imap_session(account) as client:
        client.select_folder(message.folder.name)
        _expunge(client, message.imap_uid)


def _expunge(client, uid):
    """Flag one message of the selected mailbox ``\\Deleted`` and expunge it.

    With UIDPLUS (RFC 4315) the expunge names the UID and takes nothing
    else. Without it there is only the plain EXPUNGE, which also removes
    every other message of the mailbox carrying ``\\Deleted``: the ones
    another mail program marked without expunging yet.
    """
    client.add_flags([uid], [DELETED], silent=True)
    if client.has_capability("UIDPLUS"):
        client.uid_expunge([uid])
    else:
        client.expunge()


def move_message(account, message, target_folder):
    """Move a message to another folder.

    One MOVE (RFC 6851) when the server has it: the message is never in
    both folders nor in neither. Otherwise a COPY, then the source is
    expunged - only once the copy is known to have succeeded, since the
    other order loses the message when the target refuses it (gone, over
    quota, read-only).

    Returns the UID the message got in `target_folder`, or None when the
    server did not say (no UIDPLUS) and no single copy carrying its
    Message-ID turned up there either.
    """
    uid = message.imap_uid
    with imap_session(account) as client:
        uid_next = _uid_next(client, target_folder.name)
        client.select_folder(message.folder.name)
        last_response_code(client, "COPYUID")
        if client.has_capability("MOVE"):
            client.move([uid], target_folder.name)
            copied_uid = _copied_uid(client, uid)
        else:
            client.copy([uid], target_folder.name)
            copied_uid = _copied_uid(client, uid)
            _expunge(client, uid)
        if copied_uid is None:
            copied_uid = _find_copy(
                account, target_folder.name, message.message_id, uid_next
            )
        return copied_uid


def _uid_next(client, mailbox):
    """The UID the next message landing in `mailbox` will get, or None."""
    try:
        return client.folder_status(mailbox, ["UIDNEXT"]).get(b"UIDNEXT")
    except ConnectionLost:
        # Nothing has moved yet: the move fails as it would on any dead
        # connection, and the session drops this one.
        raise
    except IMAPClientError:
        return None


def _copied_uid(client, source_uid):
    """The target UID from the COPYUID code (RFC 4315) of the last COPY or MOVE.

    ``COPYUID <uidvalidity> <source set> <target set>``; a single message
    copied gives a single UID on each side.
    """
    match last_response_code(client, "COPYUID"):
        case (int(), int(source), int(target)) if source == source_uid:
            return target
    return None


def _find_copy(account, mailbox, message_id, uid_next):
    """The UID of the one message at or past `uid_next` carrying `message_id`.

    The copy just made is the only message that can have both. Anything else
    - no Message-ID to look for, no UIDNEXT to bound the search, two hits -
    answers None rather than guess: a wrong UID would point later flags and
    deletes at another message.

    A lookup that fails answers None too: the message has moved by now, and
    an error here would report a move that happened as one that did not.
    """
    message_id = message_id.strip()
    if not message_id or not uid_next or not _searchable(message_id):
        return None
    try:
        # A block of its own, so that a connection dying under the lookup is
        # dropped by the session before the error is swallowed here.
        with imap_session(account) as client:
            client.select_folder(mailbox, readonly=True)
            found = client.search(
                ["UID", f"{uid_next}:*", "HEADER", "Message-ID", _astring(message_id)]
            )
    except IMAPClientError, OSError:
        return None
    # "n:*" always matches the highest UID, even when it is below n.
    found = [uid for uid in found if uid >= uid_next]
    return found[0] if len(found) == 1 else None


def _astring(value):
    """`value` as a SEARCH argument no server can read as anything else.

    IMAPClient quotes an argument only when it holds a space, a quote or a
    backslash. A Message-ID comes off the wire and may hold what an atom
    cannot: ``x{20}`` announces a literal the server then waits for, and a
    ``%``, ``*`` or ``]`` is answered BAD. Sent as a literal, the bytes are
    counted and never interpreted.
    """
    return literal(value.encode("ascii"))


def _searchable(message_id):
    """Whether a Message-ID can be looked for at all: a SEARCH without a
    charset takes ASCII, and no header holds a control character."""
    return message_id.isascii() and message_id.isprintable()


def append_to_sent(account, message_file, msg_id):
    """Append a sent message to the account's Sent folder via IMAP APPEND.

    `message_file` is a real file holding the whole message with CRLF line
    endings. Checks first whether the server already auto-copied the message
    (Gmail, Outlook, etc.) by searching for its Message-ID to avoid duplicates.
    Raises when the server refuses the copy.
    """
    from ..models import MailFolder
    from ..queries import special_folder

    sent_folder = special_folder(account, MailFolder.FolderType.SENT)
    if not sent_folder:
        logger.warning(
            "No Sent folder found for %s, skipping APPEND", scrub(account.email)
        )
        return

    with imap_session(account) as client:
        client.select_folder(sent_folder.name, readonly=True)
        if (
            msg_id
            and _searchable(msg_id)
            and client.search(["HEADER", "Message-ID", _astring(msg_id)])
        ):
            logger.info(
                "Message already in Sent for %s (auto-copied by server)",
                scrub(account.email),
            )
            return

        append_file(client, sent_folder.name, [SEEN], message_file)
        logger.info(
            "Appended sent message to %s for %s",
            scrub(sent_folder.name),
            scrub(account.email),
        )


def save_draft(account, raw_message_bytes, old_uid=None):
    """Save a draft message to the account's Drafts folder via IMAP APPEND.

    If old_uid is provided, deletes the previous draft once the new one is
    stored. Returns the created MailMessage, or None when the account has
    no Drafts folder or the server refused the draft.
    """
    from workspace.mail.services.imap_sync import sync_folder_messages

    from ..models import MailFolder, MailMessage
    from ..queries import special_folder

    drafts_folder = special_folder(account, MailFolder.FolderType.DRAFTS)
    if not drafts_folder:
        logger.warning(
            "No Drafts folder found for %s, skipping save_draft", scrub(account.email)
        )
        return None

    # Read before the network call, to find our exact draft after the sync.
    # "The newest draft" is unsafe: another session (mobile, web client)
    # could APPEND a different one between our APPEND and our sync.
    headers = BytesHeaderParser(policy=policy.default).parsebytes(
        raw_message_bytes.partition(b"\r\n\r\n")[0]
    )
    msg_id = str(headers.get("Message-ID", "")).strip()

    with imap_session(account) as client:
        # The new draft first, the old one deleted only once it is stored.
        # The reverse order risks losing both copies if the APPEND fails
        # (network drop, quota exceeded), since an expunge is irreversible.
        try:
            client.append(
                drafts_folder.name,
                raw_message_bytes,
                flags=[DRAFT, SEEN],
                msg_time=dj_timezone.now(),
            )
        except ConnectionLost:
            raise
        except IMAPClientError as e:
            logger.warning(
                "IMAP APPEND draft to %s failed for %s: %s",
                scrub(drafts_folder.name),
                scrub(account.email),
                scrub(e),
            )
            return None

        if old_uid:
            client.select_folder(drafts_folder.name)
            _expunge(client, old_uid)

        logger.info(
            "Saved draft to %s for %s", scrub(drafts_folder.name), scrub(account.email)
        )

        # Sync to pick up the new message locally
        sync_folder_messages(account, drafts_folder)

    qs = MailMessage.objects.filter(folder=drafts_folder, deleted_at__isnull=True)
    if msg_id:
        # Identify the exact draft we just appended.
        return qs.filter(message_id=msg_id).first()
    # A draft built without a Message-ID header is anomalous; the newest
    # draft is the best available guess for the one just appended.
    return qs.order_by("-created_at").first()


def delete_draft(account, message):
    """Delete a draft message from the IMAP server and locally."""
    with imap_session(account) as client:
        client.select_folder(message.folder.name)
        _expunge(client, message.imap_uid)

    message.deleted_at = dj_timezone.now()
    message.save(update_fields=["deleted_at", "updated_at"])
