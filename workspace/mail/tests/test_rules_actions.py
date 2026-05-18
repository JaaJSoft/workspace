from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import (
    MailAccount, MailFolder, MailLabel, MailMessage, MailMessageLabel,
)
from workspace.mail.services.rules.actions import apply_action
from workspace.mail.services.rules.schema import parse_actions

User = get_user_model()


class LabelActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='au', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='a@x.com',
            imap_host='x', smtp_host='x', username='a@x.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
        )
        # Use one of the auto-seeded default labels for this account.
        self.label = self.account.labels.first()

    def test_add_label(self):
        action = parse_actions([{'type': 'add_label', 'label_id': str(self.label.uuid)}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])
        self.assertEqual(result['type'], 'add_label')
        self.assertTrue(
            MailMessageLabel.objects.filter(message=self.msg, label=self.label).exists(),
        )

    def test_add_label_twice_is_idempotent(self):
        action = parse_actions([{'type': 'add_label', 'label_id': str(self.label.uuid)}])[0]
        apply_action(action, self.msg)
        # Second call must not raise and the link must still exist exactly once.
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])
        self.assertEqual(
            MailMessageLabel.objects.filter(message=self.msg, label=self.label).count(),
            1,
        )

    def test_add_label_unknown_returns_not_ok(self):
        import uuid
        action = parse_actions([{'type': 'add_label', 'label_id': str(uuid.uuid4())}])[0]
        result = apply_action(action, self.msg)
        self.assertFalse(result['ok'])
        self.assertIn('label_not_found', result['error'])

    def test_remove_label(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        action = parse_actions([{'type': 'remove_label', 'label_id': str(self.label.uuid)}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])
        self.assertFalse(
            MailMessageLabel.objects.filter(message=self.msg, label=self.label).exists(),
        )
