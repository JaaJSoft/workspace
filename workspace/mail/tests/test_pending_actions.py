from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class MailPendingActionProviderTests(TestCase):
    """Tests for the mail pending action provider."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mailuser', email='mail@test.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='mail@test.com',
            imap_host='imap.test.com',
            smtp_host='smtp.test.com',
            username='mail@test.com',
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            display_name='Inbox',
            folder_type=MailFolder.FolderType.INBOX,
        )

    def test_pending_actions_returns_unread_email_count(self):
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=1, subject='Hello', is_read=False,
        )
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=2, subject='World', is_read=False,
        )
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=3, subject='Read', is_read=True,
        )

        from workspace.core.module_registry import registry
        counts = registry.get_pending_action_counts(self.user)
        self.assertEqual(counts.get('mail'), 2)

    def test_pending_actions_returns_zero_when_all_read(self):
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=1, subject='Read', is_read=True,
        )

        from workspace.core.module_registry import registry
        counts = registry.get_pending_action_counts(self.user)
        self.assertEqual(counts.get('mail'), 0)

    def test_pending_actions_excludes_deleted_messages(self):
        from django.utils import timezone
        MailMessage.objects.create(
            account=self.account, folder=self.inbox,
            imap_uid=1, subject='Deleted', is_read=False,
            deleted_at=timezone.now(),
        )

        from workspace.core.module_registry import registry
        counts = registry.get_pending_action_counts(self.user)
        self.assertEqual(counts.get('mail'), 0)
