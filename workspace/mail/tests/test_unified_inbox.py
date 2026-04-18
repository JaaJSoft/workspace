from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()

URL = '/api/v1/mail/messages'


class UnifiedInboxTestMixin:
    """Shared setup: two accounts with inbox folders and messages."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mailuser', email='mail@test.com', password='pass123',
        )
        self.other_user = User.objects.create_user(
            username='other', email='other@test.com', password='pass123',
        )

        # --- user accounts ---
        self.account1 = MailAccount.objects.create(
            owner=self.user,
            email='alice@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='alice@example.com',
        )
        self.account2 = MailAccount.objects.create(
            owner=self.user,
            email='bob@work.com',
            imap_host='imap.work.com',
            smtp_host='smtp.work.com',
            username='bob@work.com',
        )

        # --- other user account ---
        self.other_account = MailAccount.objects.create(
            owner=self.other_user,
            email='eve@evil.com',
            imap_host='imap.evil.com',
            smtp_host='smtp.evil.com',
            username='eve@evil.com',
        )

        # --- inbox folders ---
        self.inbox1 = MailFolder.objects.create(
            account=self.account1, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.inbox2 = MailFolder.objects.create(
            account=self.account2, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.other_inbox = MailFolder.objects.create(
            account=self.other_account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )

        # --- non-inbox folder ---
        self.sent1 = MailFolder.objects.create(
            account=self.account1, name='Sent',
            display_name='Sent', folder_type='sent',
        )

        now = timezone.now()

        # Messages in account1 inbox
        self.msg1 = MailMessage.objects.create(
            account=self.account1, folder=self.inbox1,
            imap_uid=1, subject='Hello from account1',
            date=now, is_read=False,
        )
        self.msg2 = MailMessage.objects.create(
            account=self.account1, folder=self.inbox1,
            imap_uid=2, subject='Read message',
            date=now, is_read=True,
        )

        # Message in account2 inbox
        self.msg3 = MailMessage.objects.create(
            account=self.account2, folder=self.inbox2,
            imap_uid=1, subject='Hello from account2',
            date=now, is_read=False, is_starred=True,
        )

        # Message in sent folder (should NOT appear in unified inbox)
        self.msg_sent = MailMessage.objects.create(
            account=self.account1, folder=self.sent1,
            imap_uid=1, subject='Sent message',
            date=now,
        )

        # Message from other user (should NOT appear)
        self.msg_other = MailMessage.objects.create(
            account=self.other_account, folder=self.other_inbox,
            imap_uid=1, subject='Other user message',
            date=now,
        )

        # Soft-deleted message (should NOT appear)
        self.msg_deleted = MailMessage.objects.create(
            account=self.account1, folder=self.inbox1,
            imap_uid=99, subject='Deleted',
            date=now, deleted_at=now,
        )


class UnifiedInboxListTests(UnifiedInboxTestMixin, APITestCase):
    """Tests for GET /api/v1/mail/messages?inbox=all"""

    def test_unauthenticated_rejected(self):
        resp = self.client.get(URL, {'inbox': 'all'})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_returns_messages_from_all_inbox_folders(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        subjects = {m['subject'] for m in resp.data['results']}
        self.assertIn('Hello from account1', subjects)
        self.assertIn('Read message', subjects)
        self.assertIn('Hello from account2', subjects)
        self.assertEqual(resp.data['count'], 3)

    def test_excludes_non_inbox_folders(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all'})
        subjects = {m['subject'] for m in resp.data['results']}
        self.assertNotIn('Sent message', subjects)

    def test_excludes_other_user_messages(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all'})
        subjects = {m['subject'] for m in resp.data['results']}
        self.assertNotIn('Other user message', subjects)

    def test_excludes_soft_deleted_messages(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all'})
        subjects = {m['subject'] for m in resp.data['results']}
        self.assertNotIn('Deleted', subjects)

    def test_filter_unread(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all', 'unread': 'true'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 2)
        self.assertTrue(all(not m['is_read'] for m in resp.data['results']))

    def test_filter_starred(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all', 'starred': 'true'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['subject'], 'Hello from account2')

    def test_filter_search(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all', 'search': 'account2'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)

    def test_pagination_metadata(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL, {'inbox': 'all', 'page': '1'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('count', resp.data)
        self.assertIn('page', resp.data)
        self.assertIn('page_size', resp.data)
        self.assertEqual(resp.data['page'], 1)
        self.assertEqual(resp.data['page_size'], 50)

    def test_no_accounts_returns_empty(self):
        """User with no mail accounts gets an empty result."""
        user_no_mail = User.objects.create_user(
            username='nomail', email='nomail@test.com', password='pass123',
        )
        self.client.force_authenticate(user_no_mail)
        resp = self.client.get(URL, {'inbox': 'all'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['results'], [])

    def test_missing_params_returns_400(self):
        """Calling without folder, label, or inbox=all returns 400."""
        self.client.force_authenticate(self.user)
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
