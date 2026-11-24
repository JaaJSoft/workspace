"""SMTP service for sending emails."""

import base64
import email.policy
import html
import io
import logging
import re
import smtplib
import tempfile
import uuid
from email.generator import BytesGenerator
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from workspace.common.logging import scrub
from workspace.common.url_guard import UnsafeUrl
from workspace.mail.services import tls
from workspace.mail.services.network_policy import open_mail_socket

logger = logging.getLogger(__name__)

# Default network timeout in seconds, mirroring IMAP_TIMEOUT. Without it
# smtplib waits on the system default, which is no deadline at all: a server
# that stops answering mid-handshake pins the caller forever, and sending now
# happens inside the AI tool loop, which streams to a waiting user.
SMTP_TIMEOUT = 30


class GuardedSMTP(smtplib.SMTP):
    """smtplib on a socket that only dials an address mail may connect to.

    ``_get_socket`` is the hook smtplib gives its own TLS subclass; the
    connection security tests fail if an upgrade moves it.
    """

    def _get_socket(self, host, port, timeout):
        return open_mail_socket(host, port, timeout, self.source_address)


class GuardedSMTPSSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        sock = open_mail_socket(host, port, timeout, self.source_address)
        return self.context.wrap_socket(sock, server_hostname=self._host)


def connect_smtp(account):
    """Open and authenticate an SMTP connection for the given account.

    ``smtp_use_tls`` picks STARTTLS (on) or implicit TLS (off), and both verify
    the server's certificate before any credential is sent. A server mail may
    not connect to raises ``UnsafeUrl``.
    """
    context = tls.client_context()
    if account.smtp_use_tls:
        server = GuardedSMTP(account.smtp_host, account.smtp_port, timeout=SMTP_TIMEOUT)
        try:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        except BaseException:
            server.close()
            raise
    else:
        server = GuardedSMTPSSL(
            account.smtp_host, account.smtp_port, timeout=SMTP_TIMEOUT, context=context
        )
        server.ehlo()

    try:
        if account.auth_method == "oauth2":
            _authenticate_xoauth2(server, account)
        else:
            server.login(account.username, account.get_password())
    except BaseException:
        server.close()
        raise
    return server


def _authenticate_xoauth2(server, account):
    from workspace.mail.services.oauth2 import get_valid_access_token

    token = get_valid_access_token(account)
    if not token:
        raise RuntimeError("No valid OAuth2 access token available")
    initial_response = f"user={account.username}\x01auth=Bearer {token}\x01\x01"

    def xoauth2(challenge=None):
        # A refused token gets a 334 carrying the error as JSON; the client
        # answers it with an empty line and the server then sends its 535.
        return initial_response if challenge is None else ""

    # auth() reads the reply code and raises SMTPAuthenticationError on
    # anything but success, which a bare docmd() would drop.
    server.auth("XOAUTH2", xoauth2)


def test_smtp_connection(account):
    """Test SMTP connectivity. Returns (success, error_message); a server
    mail may not connect to raises ``UnsafeUrl`` instead."""
    try:
        server = connect_smtp(account)
        server.quit()
        return True, None
    except UnsafeUrl:
        raise
    except Exception as e:
        return False, str(e)


# Bytes of an attachment encoded per step. A multiple of 57, the payload of
# one 76-character base64 line, so only the last line of a part is short.
_BASE64_CHUNK = 57 * 1024

# Bytes handed to the socket per send while streaming DATA.
_SEND_CHUNK = 64 * 1024

_CRLF = b"\r\n"

# CRLF line endings, and bodies encoded down to 7 bits: not every server on
# the way announces 8BITMIME.
_POLICY = email.policy.SMTP.clone(cte_type="7bit")


def text_to_html(text):
    """The HTML part of a message written as plain text.

    Escaped: the text is what the user typed and what they quoted, so a
    ``<`` in it is a character, never markup sent on their behalf.
    """
    return html.escape(text).replace("\n", "<br>\n")


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

    Attachments are not read here: each part carries a placeholder instead
    of its payload, and the returned dict maps the line that placeholder
    makes in the serialized message to the attachment, for `_write_mime` to
    stream in its place.
    """
    to = to or []
    cc = cc or []
    bcc = bcc or []
    attachments = attachments or []

    msg = EmailMessage(policy=_POLICY)
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

    if body_text:
        msg.set_content(body_text)
        msg.add_alternative(body_html or text_to_html(body_text), subtype="html")
    else:
        msg.set_content(body_html, subtype="html")

    placeholders = {}
    for attachment in attachments:
        # Short enough for its base64 to be a single line of the message.
        placeholder = uuid.uuid4().hex.encode("ascii")
        maintype, subtype = _content_type(attachment)
        # The name goes in both headers, as mail programs write it: some
        # read only the Content-Type one. Outside ASCII both are encoded
        # per RFC 2231.
        msg.add_attachment(
            placeholder,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.name,
            params={"name": attachment.name},
        )
        placeholders[base64.b64encode(placeholder)] = attachment

    return msg, placeholders


_MIME_TOKEN = re.compile(r"[a-z0-9!#$&^_.+-]+")


def _content_type(attachment):
    """The ``(maintype, subtype)`` an attachment goes out with.

    What its source says of it when it says something: the `content_type`
    of an upload, or the one the caller set from a Files node's type. A
    browser that does not know sends nothing or octet-stream, and the name
    then tells more.
    """
    declared = getattr(attachment, "content_type", None)
    if not isinstance(declared, str):
        declared = ""
    declared = declared.partition(";")[0].strip().lower()
    maintype, _slash, subtype = declared.partition("/")
    if (
        not _MIME_TOKEN.fullmatch(maintype)
        or not _MIME_TOKEN.fullmatch(subtype)
        or (maintype, subtype) == ("application", "octet-stream")
    ):
        from workspace.files.services.detection import detect_from_name

        guessed = detect_from_name(attachment.name).mime_type
        maintype, _slash, subtype = guessed.partition("/")
    # A multipart or message part has a structure of its own and cannot be
    # base64: a file of that type travels as plain bytes.
    if maintype in ("multipart", "message") or not subtype:
        return "application", "octet-stream"
    return maintype, subtype


def _write_mime(fp, msg, placeholders):
    """Serialize `msg` into `fp` with CRLF line endings, streaming each
    attachment's base64 in place of its placeholder line.

    Every attachment is read exactly once, so a message needed in two
    variants is written once and the variants carved out of the output.
    """
    skeleton = io.BytesIO()
    BytesGenerator(skeleton, mangle_from_=False).flatten(msg)
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
            file.write(msg.policy.fold_binary("Bcc", ", ".join(bcc)))
        message = SentMessage(file, str(msg["Message-ID"]), outgoing_start=file.tell())
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
