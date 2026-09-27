"""A stand-in for the `smtplib.SMTP` connection `connect_smtp` returns.

It answers the commands `send_email` issues and records the transaction:
the envelope, and the message as the server would store it, dot-stuffing
undone.
"""

from email import message_from_bytes


class RecordingSMTP:
    does_esmtp = True

    def __init__(self, keep_data=True):
        self.keep_data = keep_data
        self.sender = None
        self.mail_options = None
        self.recipients = []
        self.data = b""
        self.bytes_sent = 0
        self.transactions = 0
        self.quit_called = False

    def ehlo_or_helo_if_needed(self):
        pass

    def has_extn(self, name):
        return name == "size"

    def mail(self, sender, options=()):
        self.sender = sender
        self.mail_options = list(options)
        return 250, b"OK"

    def rcpt(self, recipient, options=()):
        self.recipients.append(recipient)
        return 250, b"OK"

    def docmd(self, cmd, args=""):
        assert cmd == "DATA", cmd
        return 354, b"Go ahead"

    def send(self, chunk):
        self.bytes_sent += len(chunk)
        if self.keep_data:
            self.data += chunk

    def getreply(self):
        self.transactions += 1
        return 250, b"Queued"

    def quit(self):
        self.quit_called = True

    @property
    def raw_message(self):
        assert self.data.endswith(b"\r\n.\r\n"), "DATA was not terminated"
        lines = self.data[: -len(b".\r\n")].splitlines(keepends=True)
        return b"".join(line[1:] if line.startswith(b"..") else line for line in lines)

    @property
    def message(self):
        return message_from_bytes(self.raw_message)
