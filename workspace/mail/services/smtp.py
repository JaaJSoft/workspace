"""SMTP service for sending emails."""

import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

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


def send_email(account, to, subject, body_html='', body_text='',
               cc=None, bcc=None, reply_to=None, attachments=None):
    """Send an email through the account's SMTP server.

    Parameters
    ----------
    account : MailAccount
    to : list[str]
    subject : str
    body_html : str
    body_text : str
    cc : list[str] | None
    bcc : list[str] | None
    reply_to : str | None
    attachments : list[UploadedFile] | None
    """
    cc = cc or []
    bcc = bcc or []
    attachments = attachments or []

    # Build MIME message with all required headers
    msg = MIMEMultipart('mixed')
    msg['From'] = f'{account.display_name or account.email} <{account.email}>'
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
        # If only text was provided, also attach as html (wrapped in <pre>)
        body_part.attach(MIMEText(f'<pre>{body_text}</pre>', 'html', 'utf-8'))
    msg.attach(body_part)

    # Attachments
    for attachment in attachments:
        part = MIMEApplication(attachment.read(), Name=attachment.name)
        part['Content-Disposition'] = f'attachment; filename="{attachment.name}"'
        msg.attach(part)

    # All recipients
    all_recipients = to + cc + bcc

    msg_string = msg.as_string()

    server = connect_smtp(account)
    try:
        server.sendmail(account.email, all_recipients, msg_string)
    finally:
        server.quit()

    logger.info("Email sent from %s to %s: %s", account.email, to, subject)
    return msg_string.encode('utf-8')
