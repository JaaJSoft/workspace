"""Prefill an outgoing message from the one it answers or forwards.

The one place replies and forwards are built: the compose dialog asks for
them through `GET /api/v1/mail/messages/<uuid>/compose`, and the assistant
calls `build_reply` directly, so a draft written by either reads the same.

Threading headers are *not* built here - `services/threads.reply_headers`
owns them, because they must be derived from a stored message rather than
from anything a caller supplies.
"""

from typing import NamedTuple
from uuid import UUID

from django.utils import formats, translation
from django.utils.translation import gettext, pgettext

from workspace.users.services.settings import get_user_language, get_user_timezone

from ..models import MailAccount, MailAttachment
from .imap_parse import sanitize_mail_html
from .inline_images import InlineImages

# The composer finds the quote by this line to put the signature above it
# (`mailSignature.applySignature`).
QUOTE_MARKER = "\n\n---\n"


class Prefill(NamedTuple):
    """What the composer opens with, or what the assistant saves as a draft."""

    account: MailAccount
    to: list[str]
    cc: list[str]
    subject: str
    # The whole text: what was written, the signature, then the quote.
    body_text: str
    # The signature exactly as `body_text` carries it, so the composer can
    # swap it when the user picks another account.
    signature_block: str
    reply_message_id: UUID | None
    attachments: list[MailAttachment]


def reply_subject(subject):
    """Prefix with ``Re:`` unless the subject already carries one."""
    return _prefixed(subject, "Re:", ("re:",))


def forward_subject(subject):
    """Prefix with ``Fwd:`` unless the subject already carries one."""
    return _prefixed(subject, "Fwd:", ("fwd:", "fw:"))


def _prefixed(subject, prefix, known):
    subject = (subject or "").strip()
    if not subject:
        return prefix
    if subject.casefold().startswith(known):
        return subject
    return f"{prefix} {subject}"


def signature_block(signature):
    """The block a signature takes in a body: the ``-- `` delimiter, then it."""
    signature = (signature or "").strip()
    return f"\n-- \n{signature}\n" if signature else ""


def _address(entry):
    """The bare email out of a stored ``{name, email}`` recipient entry."""
    if isinstance(entry, dict):
        return (entry.get("email") or "").strip()
    return str(entry or "").strip()


def _dedupe(addresses, exclude):
    """Order-preserving dedup, case-insensitive, minus the `exclude` set."""
    seen = {}
    for address in addresses:
        if not address:
            continue
        key = address.casefold()
        if key in exclude or key in seen:
            continue
        seen[key] = address
    return list(seen.values())


def _recipients(message, account, reply_all):
    """The To and Cc of a reply, never the account's own address.

    Answering a message the account sent itself (from the Sent folder)
    writes again to the people it was sent to, as mail clients do.
    """
    own = {account.email.casefold()} if account.email else set()
    sender = (message.from_email or "").strip()
    addressed = [_address(entry) for entry in message.to_addresses]

    if sender.casefold() in own:
        to = addressed
    elif reply_all:
        to = [sender, *addressed]
    else:
        to = [sender]
    to = _dedupe(to, own)

    cc = []
    if reply_all:
        cc = _dedupe(
            (_address(entry) for entry in message.cc_addresses),
            own | {address.casefold() for address in to},
        )
    return to, cc


def _quote_date(message, user_tz):
    if not message.date:
        return gettext("an earlier date")
    # Translators: Django date format of the "On <date>, <sender> wrote:" line
    # that opens the quote of a reply, "Wednesday, March 4, 2026, 10:15".
    return formats.date_format(
        message.date.astimezone(user_tz),
        pgettext("reply quote date", "l, F j, Y, H:i"),
    )


def _sender_name(message):
    return message.from_name or message.from_email or pgettext("mail sender", "Unknown")


def _reply_quote(message, user_tz):
    """The quoted-parent block that closes a reply body."""
    header = gettext("On %(date)s, %(sender)s wrote:") % {
        "date": _quote_date(message, user_tz),
        "sender": _sender_name(message),
    }
    original = message.body_text or message.snippet or ""
    quoted = "\n".join(f"> {line}" for line in original.splitlines()) or ">"
    return f"{QUOTE_MARKER}{header}\n{quoted}"


def _forward_quote(message):
    header = gettext("Forwarded message from %(sender)s:") % {
        "sender": _sender_name(message),
    }
    original = message.body_text or message.snippet or ""
    return f"{QUOTE_MARKER}{header}\n\n{original}"


def _forwarded_attachments(message):
    """Every attachment of `message` but the pictures its body displays.

    The forwarded text does not show them, so they would arrive as stray files.
    """
    attachments = list(message.attachments.all())
    inline_images = InlineImages(attachments)
    sanitize_mail_html(message.body_html, attribute_filter=inline_images.note)
    return [a for a in attachments if a.uuid not in inline_images.embedded]


def _body(body, signature, quote):
    return f"{(body or '').rstrip()}{signature}{quote}"


def build_reply(message, user, *, reply_all=False, body=""):
    """Return the `Prefill` answering `message`, for `user` to send.

    `reply_all` widens the recipients to everyone the parent addressed.
    `body` is what the reply says above its signature and the quote; the
    composer leaves it empty for the user to type.
    """
    account = message.account
    to, cc = _recipients(message, account, reply_all)
    signature = signature_block(account.signature)
    with translation.override(get_user_language(user)):
        quote = _reply_quote(message, get_user_timezone(user))
    return Prefill(
        account=account,
        to=to,
        cc=cc,
        subject=reply_subject(message.subject),
        body_text=_body(body, signature, quote),
        signature_block=signature,
        reply_message_id=message.uuid,
        attachments=[],
    )


def build_forward(message, user, *, body=""):
    """Return the `Prefill` forwarding `message` with its attachments."""
    account = message.account
    signature = signature_block(account.signature)
    with translation.override(get_user_language(user)):
        quote = _forward_quote(message)
    return Prefill(
        account=account,
        to=[],
        cc=[],
        subject=forward_subject(message.subject),
        body_text=_body(body, signature, quote),
        signature_block=signature,
        reply_message_id=None,
        attachments=_forwarded_attachments(message),
    )
