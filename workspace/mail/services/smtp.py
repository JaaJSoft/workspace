"""SMTP service for sending emails."""

import base64
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import NamedTuple

logger = logging.getLogger(__name__)


def connect_smtp(account):
    """Open and authenticate an SMTP connection for the given account."""
    if account.smtp_use_tls:
        server = smtplib.SMTP(account.smtp_host, account.smtp_port)
        server.ehlo()
        server.starttls()
        server.ehlo()
    else:
        server = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port)
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
    """Assemble the MIME message object. See build_draft_message for the
    parameters.

    Each attachment is read exactly once here, so a caller that needs two
    serializations of the same message must build it once and re-serialize
    the returned object rather than calling this twice.
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

    for attachment in attachments:
        part = MIMEApplication(attachment.read(), Name=attachment.name)
        part["Content-Disposition"] = f'attachment; filename="{attachment.name}"'
        msg.attach(part)

    return msg


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
    msg = _build_mime(
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
    return msg.as_string().encode("utf-8")


class SentMessage(NamedTuple):
    """The two byte variants of a message that was just sent.

    They differ by the Bcc header alone and share everything else,
    Message-ID included, so the archived copy threads with the replies the
    outgoing one attracts.
    """

    outgoing: bytes
    """Handed to sendmail - no Bcc header, the list is in the envelope."""

    archived: bytes
    """Handed to IMAP APPEND - carries Bcc so Sent records who got a copy."""


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

    Returns a `SentMessage` carrying both serializations: the bytes that
    went out (no Bcc header) and the ones to archive in Sent (Bcc header
    included). The message is assembled once because the attachment
    streams can only be read once.
    """
    cc = cc or []
    bcc = bcc or []

    msg = _build_mime(
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
    outgoing = msg.as_string().encode("utf-8")

    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    archived = msg.as_string().encode("utf-8")

    all_recipients = to + cc + bcc

    server = connect_smtp(account)
    try:
        server.sendmail(account.email, all_recipients, outgoing.decode("utf-8"))
    finally:
        server.quit()

    logger.info("Email sent from %s to %s: %s", account.email, to, subject)
    return SentMessage(outgoing=outgoing, archived=archived)
