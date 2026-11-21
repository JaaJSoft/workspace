"""Reading a message off the wire into a MailMessage."""

import email
import email.policy
import logging
from datetime import UTC, datetime

import nh3
from django.core.files.base import ContentFile
from django.db import transaction

from workspace.common.logging import scrub

from .addresses import sender_columns
from .html_text import html_to_text
from .imap_messages import DRAFT, FLAGGED, SEEN

logger = logging.getLogger(__name__)

# HTML sanitisation whitelist
NH3_ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

# class and style stay: the body is rendered in a frame of its own, which the
# app's stylesheets do not reach and out of which no style can draw.
NH3_ALLOWED_ATTRIBUTES = {
    "*": {"class", "style", "dir", "lang"},
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height", "title"},
    "td": {"colspan", "rowspan", "align", "valign"},
    "th": {"colspan", "rowspan", "align", "valign"},
    "table": {"border", "cellpadding", "cellspacing", "width"},
}

# Every link leaves the message's frame for a new tab: a target naming the
# frame itself would load the linked page in the middle of the app.
NH3_SET_ATTRIBUTES = {"a": {"target": "_blank"}}

# cid: points at a part of the message itself (RFC 2392). The stored body keeps
# it; services/inline_images resolves it on read.
NH3_URL_SCHEMES = nh3.ALLOWED_URL_SCHEMES | {"cid"}


def sanitize_mail_html(html, inline_images=None):
    """Clean an email's HTML body down to what the message frame renders.

    nh3 also gives every link ``rel="noopener noreferrer"``. ``cid:``
    references are kept as they are, unless ``inline_images`` (an
    ``InlineImages`` of the message) is given to resolve them.
    """
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=NH3_ALLOWED_TAGS,
        attributes=NH3_ALLOWED_ATTRIBUTES,
        set_tag_attribute_values=NH3_SET_ATTRIBUTES,
        url_schemes=NH3_URL_SCHEMES,
        attribute_filter=inline_images.resolve if inline_images is not None else None,
    )


def _text(value):
    """`value` as text a database stores.

    The email package keeps the bytes it could not decode as lone
    surrogates, which no UTF-8 column takes: they are read as UTF-8 where
    they happen to be (a header written raw, without an encoded word) and
    replaced where they are not. PostgreSQL refuses a NUL.
    """
    text = str(value)
    try:
        text = text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeEncodeError:
        # A surrogate that stands for no byte.
        text = text.encode("utf-8", "replace").decode("utf-8")
    return text.replace("\x00", "")


def _header(msg, name):
    """Header `name` as text: decoded and unfolded, "" when absent."""
    values = _headers(msg, name)
    return _text(values[0]) if values else ""


def _headers(msg, name):
    """Every `name` header of `msg`, parsed.

    The package's header parsers are strict readers of a lenient world, and
    a header one of them chokes on must not cost the whole message: it is
    then read as the plain text it came as.
    """
    try:
        return msg.get_all(name, [])
    except Exception:
        logger.warning("Unreadable %s header, kept as plain text", scrub(name))
        return [
            " ".join(str(value).split())
            for key, value in msg.raw_items()
            if key.lower() == name.lower()
        ]


def _addresses(msg, name):
    """The addresses of every `name` header, as ``[{name, email}, ...]``.

    Group syntax is flattened to its members, and an entry without an
    address (``<>``, a group with nobody in it) is left out.
    """
    result = []
    for header in _headers(msg, name):
        for address in getattr(header, "addresses", ()):
            # Without a domain the address is whatever stood there, taken as
            # written: `addr_spec` would quote it.
            spec = address.addr_spec if address.domain else address.username
            if spec:
                result.append(
                    {"name": _text(address.display_name), "email": _text(spec)}
                )
    return result


def _date(msg):
    """When the message says it was written, else now."""
    headers = _headers(msg, "Date")
    parsed = getattr(headers[0], "datetime", None) if headers else None
    if parsed is None:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        # "-0000": a time in UTC whose author's zone is not known.
        return parsed.replace(tzinfo=UTC)
    try:
        parsed.astimezone(UTC)
    except OverflowError:
        # Year 1 or 9999 with an offset that leaves the calendar.
        return datetime.now(UTC)
    return parsed


def _body_part(msg, subtype):
    """The part holding the message's text/`subtype` body, or None."""
    if not msg.is_multipart():
        # A message that is one part is its own body, of whatever text type.
        is_html = msg.get_content_type() == "text/html"
        return msg if is_html == (subtype == "html") else None
    return msg.get_body(preferencelist=(subtype,))


def _decoded(part):
    """The text of `part`, whatever it says its charset is."""
    if part is None:
        return ""
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except LookupError:
        # A label Python has no codec for ("unknown-8bit").
        text = payload.decode("utf-8", errors="replace")
    return _text(text)


def _attachment(part, is_inline=False):
    """The fields of the MailAttachment `part` makes, or None for a part
    without content of its own."""
    payload = part.get_payload(decode=True)
    # Empty bytes are a valid zero-byte attachment; None is no payload at all.
    if payload is None:
        return None
    content_type = part.get_content_type()
    filename = _text(part.get_filename() or "").strip()
    if not filename:
        filename = f"attachment.{content_type.split('/')[-1]}"
    return {
        "filename": filename,
        "content_type": content_type,
        "data": payload,
        "content_id": _header(part, "Content-ID").strip().strip("<>"),
        "is_inline": is_inline,
    }


def _invitation(part):
    """A text/calendar part as the ``invite.ics`` the calendar reads."""
    ics = _decoded(part)
    if not ics:
        return None
    return {
        "filename": "invite.ics",
        "content_type": "text/calendar",
        "data": ics.encode("utf-8"),
    }


@transaction.atomic
def _parse_message(raw_email, account, folder, uid, flags, known_uids=None):
    """Parse a raw email and save it as a MailMessage.

    ``flags`` are the IMAP flags the message carries, as text.

    ``known_uids`` is the set of IMAP UIDs already present in this folder,
    preloaded by the sync loop for its whole FETCH batch; membership then
    replaces the per-message existence query. Callers without a batch at
    hand can omit it and keep the single-row check.
    """
    from ..models import MailAttachment, MailMessage

    # Check if already exists
    if known_uids is not None:
        if uid in known_uids:
            return None
    elif MailMessage.objects.filter(folder=folder, imap_uid=uid).exists():
        return None

    msg = email.message_from_bytes(raw_email, policy=email.policy.default)

    # Headers
    senders = _addresses(msg, "From")
    from_addr = senders[0] if senders else {"name": "", "email": ""}
    # Fallback for sent folder: server auto-copy may strip the From header
    if not from_addr["email"] and folder.folder_type == "sent":
        from_addr = {"name": account.display_name, "email": account.email}
    # Folded across lines by most MUAs; collapse to single-space separated ids.
    references = " ".join(_header(msg, "References").split())

    # Body
    text_part = _body_part(msg, "plain")
    html_part = _body_part(msg, "html")
    body_text = _decoded(text_part)
    body_html = _decoded(html_part)

    attachments_data = []
    has_calendar_event = False
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart() or part is text_part or part is html_part:
                continue
            if part.get_content_disposition() == "attachment":
                attachment = _attachment(part)
            elif part.get_content_type() == "text/calendar":
                attachment = _invitation(part)
                has_calendar_event = has_calendar_event or attachment is not None
            elif _header(part, "Content-ID"):
                attachment = _attachment(part, is_inline=True)
            else:
                continue
            if attachment is not None:
                attachments_data.append(attachment)

    body_html = sanitize_mail_html(body_html)
    if not body_text:
        body_text = html_to_text(body_html)

    # Snippet from text body
    snippet = ""
    if body_text:
        snippet = body_text[:300].replace("\n", " ").strip()

    # Flags
    is_read = SEEN in flags
    # Messages in Sent/Drafts folders are always "read" (user wrote them)
    if folder.folder_type in ("sent", "drafts"):
        is_read = True

    mail_msg = MailMessage.objects.create(
        account=account,
        folder=folder,
        message_id=_header(msg, "Message-ID").strip()[:512],
        imap_uid=uid,
        in_reply_to=_header(msg, "In-Reply-To").strip()[:512],
        references=references,
        subject=_header(msg, "Subject")[:1000],
        **sender_columns(from_addr),
        to_addresses=_addresses(msg, "To"),
        cc_addresses=_addresses(msg, "Cc"),
        bcc_addresses=_addresses(msg, "Bcc"),
        reply_to=_header(msg, "Reply-To")[:255],
        date=_date(msg),
        snippet=snippet,
        body_text=body_text,
        body_html=body_html,
        is_read=is_read,
        is_starred=FLAGGED in flags,
        is_draft=DRAFT in flags,
        has_attachments=bool(attachments_data),
        has_calendar_event=has_calendar_event,
    )

    # Save attachments
    for att_data in attachments_data:
        MailAttachment.objects.create(
            message=mail_msg,
            filename=att_data["filename"][:255],
            content_type=att_data["content_type"][:255],
            size=len(att_data["data"]),
            content=ContentFile(att_data["data"], name=att_data["filename"]),
            content_id=att_data.get("content_id", "")[:255],
            is_inline=att_data.get("is_inline", False),
        )

    return mail_msg
