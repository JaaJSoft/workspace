from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from workspace.mail.models import MailAccount, MailLabel, MailMessageLabel, MailFolder, MailMessage

User = get_user_model()


class MailLabelModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='labeluser', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='label@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='label@test.com',
        )

    def test_create_label(self):
        label = MailLabel.objects.create(
            account=self.account, name='Custom1', color='error', icon='alert-triangle',
        )
        self.assertEqual(label.name, 'Custom1')
        self.assertEqual(label.color, 'error')
        self.assertEqual(label.account, self.account)
        self.assertEqual(label.position, 0)

    def test_unique_name_per_account(self):
        MailLabel.objects.create(account=self.account, name='Duplicate')
        with self.assertRaises(IntegrityError):
            MailLabel.objects.create(account=self.account, name='Duplicate')

    def test_same_name_different_accounts(self):
        user2 = User.objects.create_user(username='labeluser2', password='pass123')
        account2 = MailAccount.objects.create(
            owner=user2, email='label2@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='label2@test.com',
        )
        MailLabel.objects.create(account=self.account, name='Custom')
        MailLabel.objects.create(account=account2, name='Custom')
        self.assertEqual(MailLabel.objects.filter(name='Custom').count(), 2)

    def test_ordering(self):
        MailLabel.objects.create(account=self.account, name='Zebra', position=20)
        MailLabel.objects.create(account=self.account, name='Alpha', position=10)
        MailLabel.objects.create(account=self.account, name='Beta', position=10)
        labels = list(
            MailLabel.objects.filter(account=self.account, position__gte=10)
            .values_list('name', flat=True)
        )
        self.assertEqual(labels, ['Alpha', 'Beta', 'Zebra'])

    def test_cascade_delete_account(self):
        MailLabel.objects.create(account=self.account, name='Custom')
        account_uuid = self.account.uuid
        self.account.delete()
        self.assertEqual(MailLabel.objects.filter(account_id=account_uuid).count(), 0)


class MailMessageLabelModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='mmluser', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='mml@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='mml@test.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            subject='Test',
        )
        # Use a custom label to avoid collision with seeded defaults
        self.label = MailLabel.objects.create(
            account=self.account, name='CustomTag', color='accent',
        )

    def test_assign_label_to_message(self):
        link = MailMessageLabel.objects.create(message=self.msg, label=self.label)
        self.assertEqual(link.message, self.msg)
        self.assertEqual(link.label, self.label)

    def test_unique_message_label(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        with self.assertRaises(IntegrityError):
            MailMessageLabel.objects.create(message=self.msg, label=self.label)

    def test_cascade_delete_message(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        self.msg.delete()
        self.assertEqual(MailMessageLabel.objects.count(), 0)

    def test_cascade_delete_label(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        self.label.delete()
        self.assertEqual(MailMessageLabel.objects.count(), 0)

    def test_message_labels_reverse(self):
        label2 = MailLabel.objects.create(account=self.account, name='FYI2')
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        MailMessageLabel.objects.create(message=self.msg, label=label2)
        self.assertEqual(self.msg.message_labels.count(), 2)

    def test_label_links_reverse(self):
        msg2 = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=2, subject='Test2',
        )
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        MailMessageLabel.objects.create(message=msg2, label=self.label)
        self.assertEqual(self.label.label_links.count(), 2)


class DefaultLabelSeedTests(TestCase):
    def test_new_account_gets_default_labels(self):
        user = User.objects.create_user(username='seeduser', password='pass123')
        account = MailAccount.objects.create(
            owner=user, email='seed@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='seed@test.com',
        )
        labels = list(account.labels.order_by('position').values_list('name', flat=True))
        self.assertEqual(labels, ['Urgent', 'Action', 'FYI', 'Newsletter', 'Notification'])

    def test_save_existing_account_does_not_duplicate(self):
        user = User.objects.create_user(username='seeduser2', password='pass123')
        account = MailAccount.objects.create(
            owner=user, email='seed2@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='seed2@test.com',
        )
        account.display_name = 'Updated'
        account.save()
        self.assertEqual(account.labels.count(), 5)
