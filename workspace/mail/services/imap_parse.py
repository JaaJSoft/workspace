"""Email message and address parsing helpers."""

import email
import email.header
import email.utils
import re
from datetime import UTC, datetime

import nh3
from django.core.files.base import ContentFile
from django.db import transaction

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

NH3_ALLOWED_ATTRIBUTES = {
    "*": {"class", "style", "dir", "lang"},
    "a": {"href", "target", "title"},
    "img": {"src", "alt", "width", "height", "title"},
    "td": {"colspan", "rowspan", "align", "valign"},
    "th": {"colspan", "rowspan", "align", "valign"},
    "table": {"border", "cellpadding", "cellspacing", "width"},
}


def _decode_header(value):
    """Decode an RFC 2047 encoded header value."""
    if not value:
        return ""
    decoded_parts = email.header.decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _parse_address(addr_string):
    """Parse an email address string into {name, email}."""
    if not addr_string:
        return {"name": "", "email": ""}
    decoded = _decode_header(addr_string)
    name, email_addr = email.utils.parseaddr(decoded)
    if not email_addr:
        # Fallback: parseaddr fails on ambiguous formats like "user@d <user@d>"
        match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", decoded)
        if match:
            email_addr = match.group(0)
    return {"name": name, "email": email_addr}


def _parse_address_list(header_values):
    """Parse a list of header values (possibly with multiple addresses each) into [{name, email}, ...]."""
    decoded_values = [_decode_header(v) for v in header_values if v]
    result = []
    for name, addr in email.utils.getaddresses(decoded_values):
        if addr:
            result.append({"name": name, "email": addr})
    return result


def _collect_attachment(part, attachments_data, is_inline=False):
    """Extract attachment data from an email part."""
    filename = part.get_filename()
    if filename:
        filename = _decode_header(filename)
    else:
        ext = part.get_content_type().split("/")[-1]
        filename = f"attachment.{ext}"

    payload = part.get_payload(decode=True)
    # Accept empty bytes (b'') as a valid zero-byte attachment; only skip when
    # decoding produced no payload at all (None).
    if payload is not None:
        attachments_data.append(
            {
                "filename": filename,
                "content_type": part.get_content_type(),
                "data": payload,
                "content_id": (part.get("Content-ID") or "").strip("<>"),
                "is_inline": is_inline,
            }
        )


@transaction.atomic
def _parse_message(raw_email, account, folder, uid, flags_str):
    """Parse a raw email and save it as a MailMessage."""
    from ..models import MailAttachment, MailMessage

    # Check if already exists
    if MailMessage.objects.filter(folder=folder, imap_uid=uid).exists():
        return None

    msg = email.message_from_bytes(raw_email)

    # Headers
    subject = _decode_header(msg.get("Subject", ""))
    from_addr = _parse_address(msg.get("From", ""))

    # Fallback for sent folder: server auto-copy may strip the From header
    if not from_addr.get("email") and folder.folder_type == "sent":
        from_addr = {"name": account.display_name, "email": account.email}
    to_addrs = _parse_address_list(msg.get_all("To") or [])
    cc_addrs = _parse_address_list(msg.get_all("Cc") or [])
    bcc_addrs = _parse_address_list(msg.get_all("Bcc") or [])
    reply_to = msg.get("Reply-To", "")
    message_id = msg.get("Message-ID", "")
    in_reply_to = msg.get("In-Reply-To", "")

    # Date
    date_str = msg.get("Date")
    date = None
    if date_str:
        try:
            parsed = email.utils.parsedate_to_datetime(date_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            date = parsed
        except Exception:
            # Malformed Date header: leave date as None and fall back
            # to the current time below.
            pass
    if date is None:
        date = datetime.now(UTC)

    # Body
    body_text = ""
    body_html = ""
    attachments_data = []

    has_calendar_event = False

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition:
                _collect_attachment(part, attachments_data)
            elif content_type == "text/calendar":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    ics_content = payload.decode(charset, errors="replace")
                    attachments_data.append(
                        {
                            "filename": "invite.ics",
                            "content_type": "text/calendar",
                            "data": ics_content.encode("utf-8"),
                        }
                    )
                    has_calendar_event = True
            elif content_type == "text/plain" and not body_text:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_text = payload.decode(charset, errors="replace")
            elif content_type == "text/html" and not body_html:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_html = payload.decode(charset, errors="replace")
            elif part.get("Content-ID"):
                # Inline attachment
                _collect_attachment(part, attachments_data, is_inline=True)
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if content_type == "text/html":
                body_html = decoded
            else:
                body_text = decoded

    # Sanitise HTML
    if body_html:
        body_html = nh3.clean(
            body_html,
            tags=NH3_ALLOWED_TAGS,
            attributes=NH3_ALLOWED_ATTRIBUTES,
        )

    # Snippet from text body
    snippet = ""
    if body_text:
        snippet = body_text[:300].replace("\n", " ").strip()

    # Flags
    is_read = "\\Seen" in flags_str
    # Messages in Sent/Drafts folders are always "read" (user wrote them)
    if folder.folder_type in ("sent", "drafts"):
        is_read = True
    is_starred = "\\Flagged" in flags_str
    is_draft = "\\Draft" in flags_str

    mail_msg = MailMessage.objects.create(
        account=account,
        folder=folder,
        message_id=message_id[:512],
        imap_uid=uid,
        in_reply_to=in_reply_to[:512],
        subject=subject[:1000],
        from_address=from_addr,
        to_addresses=to_addrs,
        cc_addresses=cc_addrs,
        bcc_addresses=bcc_addrs,
        reply_to=reply_to[:255],
        date=date,
        snippet=snippet,
        body_text=body_text,
        body_html=body_html,
        is_read=is_read,
        is_starred=is_starred,
        is_draft=is_draft,
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
