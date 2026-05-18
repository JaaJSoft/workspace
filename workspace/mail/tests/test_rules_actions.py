from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import (
    MailAccount, MailFolder, MailLabel, MailMessage, MailMessageLabel,
)
from workspace.mail.services.rules.actions import apply_action
from workspace.mail.services.rules.schema import parse_actions

User = get_user_model()


class _BaseActionTests(TestCase):
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


class LabelActionTests(_BaseActionTests):
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


class FlagActionTests(_BaseActionTests):
    @patch('workspace.mail.services.rules.actions.mark_read')
    def test_mark_read(self, mock_imap):
        action = parse_actions([{'type': 'mark_read'}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_read)
        mock_imap.assert_called_once_with(self.account, self.msg)

    @patch('workspace.mail.services.rules.actions.mark_unread')
    def test_mark_unread(self, mock_imap):
        self.msg.is_read = True
        self.msg.save(update_fields=['is_read'])
        action = parse_actions([{'type': 'mark_unread'}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])
        self.msg.refresh_from_db()
        self.assertFalse(self.msg.is_read)
        mock_imap.assert_called_once()

    @patch('workspace.mail.services.rules.actions.star_message')
    def test_star(self, mock_imap):
        action = parse_actions([{'type': 'star'}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_starred)

    @patch('workspace.mail.services.rules.actions.unstar_message')
    def test_unstar(self, mock_imap):
        self.msg.is_starred = True
        self.msg.save(update_fields=['is_starred'])
        action = parse_actions([{'type': 'unstar'}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])
        self.msg.refresh_from_db()
        self.assertFalse(self.msg.is_starred)

    @patch('workspace.mail.services.rules.actions.mark_read', side_effect=Exception('IMAP down'))
    def test_mark_read_imap_failure_still_writes_db(self, _mock):
        """IMAP failure must not abort the DB update (existing pattern in
        views_messages.MailMessageDetailView)."""
        action = parse_actions([{'type': 'mark_read'}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result['ok'])  # DB write succeeded
        self.assertTrue(result.get('imap_warning'))
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_read)
