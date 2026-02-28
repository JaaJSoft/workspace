"""SMTP service for sending emails."""

import base64
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

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

    if account.auth_method == 'oauth2':
        from workspace.mail.services.oauth2 import get_valid_access_token
        token = get_valid_access_token(account)
        auth_string = f'user={account.username}\x01auth=Bearer {token}\x01\x01'
        server.docmd('AUTH', 'XOAUTH2 ' + base64.b64encode(auth_string.encode()).decode())
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


def build_draft_message(account, to=None, subject='', body_html='',
                        body_text='', cc=None, bcc=None, reply_to=None,
                        attachments=None):
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
    attachments : list[UploadedFile] | None
    """
    to = to or []
    cc = cc or []
    bcc = bcc or []
    attachments = attachments or []

    msg = MIMEMultipart('mixed')
    msg['From'] = formataddr((account.display_name, account.email))
    msg['To'] = ', '.join(to)
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=account.email.split('@')[-1])
    if cc:
        msg['Cc'] = ', '.join(cc)
    if reply_to:
        msg['Reply-To'] = reply_to

    # Body: multipart/alternative with text + html
    body_part = MIMEMultipart('alternative')
    if body_text:
        body_part.attach(MIMEText(body_text, 'plain', 'utf-8'))
    if body_html:
        body_part.attach(MIMEText(body_html, 'html', 'utf-8'))
    elif body_text:
        body_part.attach(MIMEText(f'<pre>{body_text}</pre>', 'html', 'utf-8'))
    msg.attach(body_part)

    for attachment in attachments:
        part = MIMEApplication(attachment.read(), Name=attachment.name)
        part['Content-Disposition'] = f'attachment; filename="{attachment.name}"'
        msg.attach(part)

    return msg.as_string().encode('utf-8')


def send_email(account, to, subject, body_html='', body_text='',
               cc=None, bcc=None, reply_to=None, attachments=None):
    """Send an email through the account's SMTP server."""
    cc = cc or []
    bcc = bcc or []

    raw_msg = build_draft_message(
        account, to=to, subject=subject, body_html=body_html,
        body_text=body_text, cc=cc, bcc=bcc, reply_to=reply_to,
        attachments=attachments,
    )

    all_recipients = to + cc + bcc

    server = connect_smtp(account)
    try:
        server.sendmail(account.email, all_recipients, raw_msg.decode('utf-8'))
    finally:
        server.quit()

    logger.info("Email sent from %s to %s: %s", account.email, to, subject)
    return raw_msg
