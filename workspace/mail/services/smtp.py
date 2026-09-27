"""SMTP service for sending emails."""

import base64
import io
import logging
import smtplib
import tempfile
import uuid
from email.generator import BytesGenerator
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Default network timeout in seconds, mirroring IMAP_TIMEOUT. Without it
# smtplib waits on the system default, which is no deadline at all: a server
# that stops answering mid-handshake pins the caller forever, and sending now
# happens inside the AI tool loop, which streams to a waiting user.
SMTP_TIMEOUT = 30


def connect_smtp(account):
    """Open and authenticate an SMTP connection for the given account."""
    if account.smtp_use_tls:
        server = smtplib.SMTP(
            account.smtp_host, account.smtp_port, timeout=SMTP_TIMEOUT
        )
        server.ehlo()
        server.starttls()
        server.ehlo()
    else:
        server = smtplib.SMTP_SSL(
            account.smtp_host, account.smtp_port, timeout=SMTP_TIMEOUT
        )
        server.ehlo()

    if account.auth_method == "oauth2":
        from workspace.mail.services.oauth2 import get_valid_access_token

        token = get_valid_access_token(account)
        auth_string = f"user={account.username}\x01auth=Bearer {token}\x01\x01"
        server.docmd(
            "AUTH", "XOAUTH2 " + base64.b64encode(auth_string.encode()).decode()
        )
    else:
        server.login(account.username, account.get_password())
    return server


def test_smtp_connection(account):
    """Test SMTP connectivity. Returns (success, error_message)."""
    try:
        server = connect_smtp(account)
        server.quit()
        return True, None
    except Exception as e:
        return False, str(e)


# Bytes of an attachment encoded per step. A multiple of 57, the payload of
# one 76-character base64 line, so only the last line of a part is short.
_BASE64_CHUNK = 57 * 1024

# Bytes handed to the socket per send while streaming DATA.
_SEND_CHUNK = 64 * 1024

_CRLF = b"\r\n"


def _build_mime(
    account,
    to=None,
    subject="",
    body_html="",
    body_text="",
    cc=None,
    bcc=None,
    reply_to=None,
    attachments=None,
    include_bcc=False,
    in_reply_to="",
    references="",
):
    """Assemble the MIME message. See build_draft_message for the parameters.

    Attachments are not read here: each part carries a unique placeholder
    line instead of its payload, and the returned dict maps that line to the
    attachment for `_write_mime` to stream in its place.
    """
    to = to or []
    cc = cc or []
    bcc = bcc or []
    attachments = attachments or []

    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((account.display_name, account.email))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=account.email.split("@")[-1])
    if cc:
        msg["Cc"] = ", ".join(cc)
    if include_bcc and bcc:
        msg["Bcc"] = ", ".join(bcc)
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    # Body: multipart/alternative with text + html
    body_part = MIMEMultipart("alternative")
    if body_text:
        body_part.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        body_part.attach(MIMEText(body_html, "html", "utf-8"))
    elif body_text:
        body_part.attach(MIMEText(f"<pre>{body_text}</pre>", "html", "utf-8"))
    msg.attach(body_part)

    placeholders = {}
    for attachment in attachments:
        part = MIMEApplication(b"", Name=attachment.name)
        part["Content-Disposition"] = f'attachment; filename="{attachment.name}"'
        placeholder = f"attachment-{uuid.uuid4().hex}"
        part.set_payload(placeholder)
        placeholders[placeholder.encode("ascii")] = attachment
        msg.attach(part)

    return msg, placeholders


def _write_mime(fp, msg, placeholders):
    """Serialize `msg` into `fp` with CRLF line endings, streaming each
    attachment's base64 in place of its placeholder line.

    Every attachment is read exactly once, so a message needed in two
    variants is written once and the variants carved out of the output.
    """
    skeleton = io.BytesIO()
    BytesGenerator(
        skeleton, mangle_from_=False, policy=msg.policy.clone(linesep="\r\n")
    ).flatten(msg)
    for line in skeleton.getvalue().splitlines(keepends=True):
        attachment = placeholders.get(line.rstrip(_CRLF))
        if attachment is None:
            fp.write(line)
        else:
            _write_base64(fp, attachment)


def _write_base64(fp, stream):
    pending = b""
    while chunk := stream.read(_BASE64_CHUNK):
        pending += chunk
        whole_lines = len(pending) - len(pending) % 57
        fp.write(base64.encodebytes(pending[:whole_lines]).replace(b"\n", _CRLF))
        pending = pending[whole_lines:]
    if pending:
        fp.write(base64.encodebytes(pending).replace(b"\n", _CRLF))


def build_draft_message(
    account,
    to=None,
    subject="",
    body_html="",
    body_text="",
    cc=None,
    bcc=None,
    reply_to=None,
    attachments=None,
    include_bcc=False,
    in_reply_to="",
    references="",
):
    """Build a MIME message and return the raw bytes.

    Parameters
    ----------
    account : MailAccount
    to : list[str] | None
    subject : str
    body_html : str
    body_text : str
    cc : list[str] | None
    bcc : list[str] | None
    reply_to : str | None
        The Reply-To header (which address should receive answers).
        Unrelated to threading - see in_reply_to for that.
    attachments : list[UploadedFile] | None
    include_bcc : bool
        Write the Bcc header into the message. Only for messages that are
        APPENDed to IMAP and re-parsed on open (drafts, the Sent copy):
        the header is the only place the Bcc list survives that round-trip,
        and both folders are readable by the account owner alone. Never set
        it on the bytes handed to SMTP, where Bcc must stay in the envelope
        to avoid leaking the hidden recipients to everyone else.
    in_reply_to : str
        Message-ID of the message being replied to.
    references : str
        Space-separated Message-ID chain of the thread, parent included.
        Both must be derived server-side from a stored message - a client
        supplied value would let a caller graft a reply onto any thread.
    """
    msg, placeholders = _build_mime(
        account,
        to=to,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        attachments=attachments,
        include_bcc=include_bcc,
        in_reply_to=in_reply_to,
        references=references,
    )
    raw = io.BytesIO()
    _write_mime(raw, msg, placeholders)
    return raw.getvalue()


class SentMessage:
    """A message that was just sent, serialized once into a temporary file.

    The file holds the copy to archive in Sent: the Bcc header first, then
    the bytes that went out. The two variants differ by that header alone
    and share everything else, Message-ID included, so the archived copy
    threads with the replies the outgoing one attracts. Close it once the
    Sent copy is filed.
    """

    def __init__(self, file, message_id, outgoing_start):
        self.file = file
        self.message_id = message_id
        self._outgoing_start = outgoing_start

    def outgoing(self):
        """The file, positioned on the bytes handed to SMTP: no Bcc header,
        the list is in the envelope."""
        self.file.seek(self._outgoing_start)
        return self.file

    def archived(self):
        """The file, positioned on the Sent copy: it carries the Bcc header,
        so Sent records who got a copy."""
        self.file.seek(0)
        return self.file

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def send_email(
    account,
    to,
    subject,
    body_html="",
    body_text="",
    cc=None,
    bcc=None,
    reply_to=None,
    attachments=None,
    in_reply_to="",
    references="",
):
    """Send an email through the account's SMTP server.

    Returns the open `SentMessage` for the caller to archive, then close.
    The message goes through a temporary file rather than memory: its
    attachments can weigh hundreds of MB, and the web worker is shared.
    """
    cc = cc or []
    bcc = bcc or []

    msg, placeholders = _build_mime(
        account,
        to=to,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        attachments=attachments,
        in_reply_to=in_reply_to,
        references=references,
    )
    # A real file, never an in-memory spool: the Sent copy is APPENDed from
    # a memory map of it.
    file = tempfile.TemporaryFile()
    try:
        if bcc:
            file.write(
                msg.policy.clone(linesep="\r\n").fold_binary("Bcc", ", ".join(bcc))
            )
        message = SentMessage(file, msg["Message-ID"], outgoing_start=file.tell())
        _write_mime(file, msg, placeholders)

        server = connect_smtp(account)
        try:
            _transmit(server, account.email, to + cc + bcc, message.outgoing())
        finally:
            server.quit()
    except BaseException:
        file.close()
        raise

    logger.info(
        "Email sent from %s to %s: %s",
        scrub(account.email),
        scrub(to),
        scrub(subject),
    )
    return message


def _transmit(server, sender, recipients, stream):
    """`smtplib.SMTP.sendmail`, reading the message from `stream`.

    sendmail wants the message as one bytes object and copies it twice more
    while dot-stuffing and terminating it. The stream is already CRLF, so
    all that is left is dot-stuffing it line by line on the way out.
    """
    server.ehlo_or_helo_if_needed()
    start = stream.tell()
    size = stream.seek(0, io.SEEK_END) - start
    stream.seek(start)
    options = []
    if server.does_esmtp and server.has_extn("size"):
        options.append(f"size={size}")

    code, resp = server.mail(sender, options)
    if code != 250:
        raise smtplib.SMTPSenderRefused(code, resp, sender)
    refused = {}
    for recipient in recipients:
        code, resp = server.rcpt(recipient)
        if code not in (250, 251):
            refused[recipient] = (code, resp)
    if len(refused) == len(recipients):
        raise smtplib.SMTPRecipientsRefused(refused)

    code, resp = server.docmd("DATA")
    if code != 354:
        raise smtplib.SMTPDataError(code, resp)
    buffer = bytearray()
    line = _CRLF
    for line in stream:
        if line.startswith(b"."):
            buffer += b"."
        buffer += line
        if len(buffer) >= _SEND_CHUNK:
            server.send(bytes(buffer))
            buffer.clear()
    if not line.endswith(_CRLF):
        buffer += _CRLF
    buffer += b"." + _CRLF
    server.send(bytes(buffer))
    code, resp = server.getreply()
    if code != 250:
        raise smtplib.SMTPDataError(code, resp)
    return refused
