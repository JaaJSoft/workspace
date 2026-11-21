"""Sending a message never holds its attachments in memory.

Sending runs inside the request, on a web worker every other request
shares: a message built, serialized and handed to the SMTP and IMAP clients as
whole bytes objects weighed several times its attachments, enough for a
few hundred MB of files to get the worker killed.
"""

import io
import tracemalloc
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail import tasks as mail_tasks
from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.sending import deliver_email
from workspace.mail.tests.imap_fake import FakeImapClient, fake_imap
from workspace.mail.tests.smtp_recorder import RecordingSMTP

User = get_user_model()

MB = 1024 * 1024
PAYLOAD = b"0123456789abcdef" * (MB // 16) * 32  # 32 MB


class StreamedSendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="streamer", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="me@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="me@example.com",
        )
        MailFolder.objects.create(
            account=self.account, name="Sent", display_name="Sent", folder_type="sent"
        )

    def test_send_and_archive_stream_the_attachment(self):
        server = RecordingSMTP(keep_data=False)
        literal_sizes = []

        def append(client, *args):
            # Measured, not read: copying the literal is what must not happen.
            literal_sizes.append(len(client.literal))
            return "OK", [b""]

        attachment = io.BytesIO(PAYLOAD)
        attachment.name = "clip.mp4"

        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        with (
            patch("workspace.mail.services.smtp.connect_smtp", return_value=server),
            fake_imap() as imap,
            patch.object(FakeImapClient, "_simple_command", append),
            patch.object(mail_tasks.sync_folder, "delay"),
        ):
            imap.mailbox("Sent")
            delivery = deliver_email(
                self.account,
                to=["bob@example.com"],
                subject="The clip",
                body_text="Attached.",
                bcc=["carol@example.com"],
                attachments=[attachment],
            )
        peak = tracemalloc.get_traced_memory()[1]

        self.assertLess(peak, 8 * MB)
        self.assertTrue(delivery.archived)
        # base64 grows the payload by a third; both copies carry all of it.
        self.assertGreater(server.bytes_sent, len(PAYLOAD) * 4 // 3)
        self.assertEqual(len(literal_sizes), 1)
        self.assertGreater(literal_sizes[0], server.bytes_sent)
