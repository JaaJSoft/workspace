"""Put a message on the wire and file the copy the user keeps.

Sending is never just SMTP: the account's own record of what it sent lives
in the Sent folder, and it is a separate IMAP round-trip that can fail on
its own. Callers get one call for the whole gesture and one flag telling
them whether the archival half worked.
"""

import logging
from typing import NamedTuple

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


class Delivery(NamedTuple):
    """What became of a send."""

    sent: object
    """The `SentMessage` returned by the SMTP layer."""

    archived: bool
    """Whether the copy reached the Sent folder. The mail went out either way."""


def deliver_email(
    account,
    to,
    subject,
    body_text="",
    body_html="",
    cc=None,
    bcc=None,
    reply_to=None,
    attachments=None,
    reply_message_id=None,
):
    """Send through SMTP, then archive the copy in Sent.

    `reply_message_id` is the UUID of the message being answered, resolved
    against `account` for the threading headers - see `threads.reply_headers`.

    Raises whatever the SMTP layer raises: a message that did not go out is
    a failure the caller has to see. A Sent copy that did not land is not -
    it comes back as `archived=False`, because the mail is already gone and
    the next folder sync usually picks it up anyway.
    """
    from .smtp import send_email
    from .threads import reply_headers

    in_reply_to, references = reply_headers(account, reply_message_id)

    sent = send_email(
        account=account,
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        attachments=attachments,
        in_reply_to=in_reply_to,
        references=references,
    )

    return Delivery(sent=sent, archived=_archive(account, sent.archived))


def _archive(account, raw_message):
    """Append the sent copy to Sent and resync it. True when it landed."""
    from ..models import MailFolder
    from .imap_messages import append_to_sent
    from .imap_sync import sync_folder_messages

    try:
        # The archived variant, not the outgoing one: it is the copy that
        # carries the Bcc header, so Sent records who actually got a copy.
        append_to_sent(account, raw_message)
    except Exception as exc:
        logger.warning(
            "Failed to append sent message to IMAP for %s: %s",
            scrub(account.email),
            scrub(str(exc)),
        )
        return False

    sent_folder = MailFolder.objects.filter(
        account=account, folder_type=MailFolder.FolderType.SENT
    ).first()
    if not sent_folder:
        return True
    try:
        sync_folder_messages(account, sent_folder)
    except Exception as exc:
        logger.warning(
            "Failed to sync sent folder after send for %s: %s",
            scrub(account.email),
            scrub(str(exc)),
        )
    return True
