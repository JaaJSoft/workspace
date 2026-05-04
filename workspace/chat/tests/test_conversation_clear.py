from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

User = get_user_model()


class ConversationClearLogSanitizationTests(APITestCase):
    """Tests for log-injection hardening in ConversationClearView's
    post-commit attachment cleanup."""

    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='G',
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.message = Message.objects.create(
            conversation=self.conv, author=self.user, body='hi',
        )
        self.attachment = MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(
                'doc.pdf', b'pdf', content_type='application/pdf',
            ),
            original_name='doc.pdf',
            mime_type='application/pdf',
            size=3,
        )
        # Inject CR/LF into the stored path. Bypass Django's storage-side
        # sanitization by writing the column directly so the FieldFile loaded
        # by the view carries the taint into the log call site.
        self.malicious_name = 'evil\r\nFAKE LOG LINE\r\nx.pdf'
        MessageAttachment.objects.filter(uuid=self.attachment.uuid).update(
            file=self.malicious_name,
        )

    def test_cleanup_log_strips_crlf_from_filename(self):
        """Regression: when storage cleanup fails on a chat attachment, the
        path was logged unsanitized. CR/LF in user-uploaded filenames could
        forge fake log lines (CWE-117 log injection). The path must pass
        through scrub() at the call site.
        """
        self.client.force_authenticate(self.user)

        with patch(
            'django.core.files.storage.default_storage.delete',
            side_effect=OSError('storage gone'),
        ):
            with self.assertLogs(
                'workspace.chat.views_typing', level='WARNING',
            ) as cm:
                with self.captureOnCommitCallbacks(execute=True):
                    resp = self.client.delete(
                        f'/api/v1/chat/conversations/{self.conv.uuid}/clear',
                    )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(cm.records), 1)
        formatted = cm.records[0].getMessage()
        self.assertNotIn('\n', formatted)
        self.assertNotIn('\r', formatted)
