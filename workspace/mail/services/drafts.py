"""Compose a message and park it in the Drafts folder.

One entry point for every path that writes a draft - the compose dialog, the
REST endpoint behind it, and the assistant's drafting tools. Two invariants
live here and nowhere else: a draft carries its Bcc in the header (the only
place it survives the IMAP round-trip), and its threading headers come from
a stored parent rather than from the caller.
"""

from ..models import MailMessage


def save_composed_draft(
    account,
    to=None,
    subject="",
    body_text="",
    body_html="",
    cc=None,
    bcc=None,
    reply_to=None,
    attachments=None,
    reply_message_id=None,
    replace_draft_uuid=None,
):
    """Build a draft, APPEND it to Drafts, and return the stored MailMessage.

    `reply_message_id` is the UUID of the message being answered, resolved
    against `account` to derive In-Reply-To / References - never a header
    value, which would let a caller graft a draft onto any thread.

    `replace_draft_uuid` is an earlier draft this one supersedes; its IMAP
    UID is expunged once the new APPEND succeeds. A UUID that no longer
    resolves is not an error - the draft was deleted from another device,
    and the new one still deserves to be saved.

    Returns None when the account has no Drafts folder, or when the APPEND
    was refused. Network failures propagate.
    """
    from .imap_messages import save_draft
    from .smtp import build_draft_message
    from .threads import reply_headers

    in_reply_to, references = reply_headers(account, reply_message_id)

    raw_message = build_draft_message(
        account,
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        attachments=attachments,
        # Drafts are re-parsed from IMAP when reopened, and both Drafts and
        # Sent are readable by the account owner alone.
        include_bcc=True,
        in_reply_to=in_reply_to,
        references=references,
    )

    old_uid = None
    if replace_draft_uuid:
        old_uid = (
            MailMessage.objects.filter(
                uuid=replace_draft_uuid, account=account, deleted_at__isnull=True
            )
            .values_list("imap_uid", flat=True)
            .first()
        )

    return save_draft(account, raw_message, old_uid=old_uid)
