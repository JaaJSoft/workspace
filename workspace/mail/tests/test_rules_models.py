from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailRule

User = get_user_model()


class MailRuleModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ruleu', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='r@x.com',
            imap_host='x', smtp_host='x', username='r@x.com',
        )

    def test_create_rule_defaults(self):
        rule = MailRule.objects.create(
            account=self.account,
            name='Newsletters to label',
            conditions={'field': 'from', 'op': 'contains', 'value': '@news.com'},
            actions=[{'type': 'mark_read'}],
        )
        self.assertTrue(rule.is_enabled)
        self.assertFalse(rule.stop_processing)
        self.assertEqual(rule.position, 0)
        self.assertEqual(rule.match_count, 0)
        self.assertIsNone(rule.last_matched_at)
        self.assertIsNotNone(rule.uuid)
        self.assertEqual(rule.actions, [{'type': 'mark_read'}])

    def test_ordering_uses_position(self):
        a = MailRule.objects.create(account=self.account, name='a', position=2)
        b = MailRule.objects.create(account=self.account, name='b', position=1)
        ordered = list(MailRule.objects.filter(account=self.account))
        self.assertEqual([r.pk for r in ordered], [b.pk, a.pk])
