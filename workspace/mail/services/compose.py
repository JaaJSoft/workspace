"""Prefill an outgoing message from the one it answers.

The reply layout mirrors what the compose dialog produces client-side
(`mail_compose.js`), so a draft written by the assistant and one started
by hand read the same in the Drafts folder.

Threading headers are *not* built here - `services/threads.reply_headers`
owns them, because they must be derived from a stored message rather than
from anything a caller supplies.
"""

from typing import NamedTuple

# A reply quotes its parent, not the whole thread. Past this many characters
# the quote is trimmed: the point is to remind the recipient what was said,
# and a draft the user still has to review should not open with fifty
# screens of history.
QUOTE_MAX_CHARS = 4000


class ReplyDraft(NamedTuple):
    """Everything a reply needs beyond its threading headers."""

    to: list[str]
    cc: list[str]
    subject: str
    body_text: str


def reply_subject(subject):
    """Prefix with ``Re:`` unless the subject already carries one."""
    subject = (subject or "").strip()
    if not subject:
        return "Re:"
    if subject[:3].casefold() == "re:":
        return subject
    return f"Re: {subject}"


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


def quote(message, user_tz):
    """Render the quoted-parent block that closes a reply body."""
    author = message.from_name or message.from_email or "unknown sender"
    when = (
        message.date.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
        if message.date
        else "an earlier date"
    )
    original = message.body_text or message.snippet or ""
    if len(original) > QUOTE_MAX_CHARS:
        original = original[:QUOTE_MAX_CHARS] + "\n[…]"
    quoted = "\n".join(f"> {line}" for line in original.splitlines()) or ">"
    return f"---\nOn {when}, {author} wrote:\n{quoted}"


def build_reply(message, account, body, user_tz, reply_all=False):
    """Return the `ReplyDraft` answering `message` from `account`.

    `reply_all` widens the recipients to everyone the parent addressed,
    minus the account's own address - answering oneself is never what the
    gesture means.
    """
    own = {account.email.casefold()} if account.email else set()

    to = [(message.from_email or "").strip()]
    cc = []
    if reply_all:
        to += [_address(entry) for entry in message.to_addresses]
        cc = _dedupe(
            (_address(entry) for entry in message.cc_addresses),
            own | {address.casefold() for address in to if address},
        )
    to = _dedupe(to, own)

    return ReplyDraft(
        to=to,
        cc=cc,
        subject=reply_subject(message.subject),
        body_text=f"{body.rstrip()}\n\n{quote(message, user_tz)}",
    )
