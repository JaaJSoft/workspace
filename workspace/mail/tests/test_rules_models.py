from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailMessage,
    MailRule,
    MailRuleLog,
)

User = get_user_model()


class MailRuleModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ruleu", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="r@x.com",
            imap_host="x",
            smtp_host="x",
            username="r@x.com",
        )

    def test_create_rule_defaults(self):
        rule = MailRule.objects.create(
            account=self.account,
            name="Newsletters to label",
            conditions={"field": "from", "op": "contains", "value": "@news.com"},
            actions=[{"type": "mark_read"}],
        )
        self.assertTrue(rule.is_enabled)
        self.assertFalse(rule.stop_processing)
        self.assertEqual(rule.position, 0)
        self.assertEqual(rule.match_count, 0)
        self.assertIsNone(rule.last_matched_at)
        self.assertIsNotNone(rule.uuid)
        self.assertEqual(rule.actions, [{"type": "mark_read"}])

    def test_ordering_uses_position(self):
        a = MailRule.objects.create(account=self.account, name="a", position=2)
        b = MailRule.objects.create(account=self.account, name="b", position=1)
        ordered = list(MailRule.objects.filter(account=self.account))
        self.assertEqual([r.pk for r in ordered], [b.pk, a.pk])


class MailRuleLogModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="logu", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="l@x.com",
            imap_host="x",
            smtp_host="x",
            username="l@x.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
        )
        self.rule = MailRule.objects.create(
            account=self.account,
            name="r1",
        )

    def test_create_log(self):
        log = MailRuleLog.objects.create(
            rule=self.rule,
            rule_name_snapshot=self.rule.name,
            message=self.msg,
            actions_applied=[{"type": "mark_read", "ok": True}],
        )
        self.assertEqual(log.rule, self.rule)
        self.assertEqual(log.actions_applied[0]["type"], "mark_read")

    def test_log_survives_rule_deletion(self):
        log = MailRuleLog.objects.create(
            rule=self.rule,
            rule_name_snapshot=self.rule.name,
            message=self.msg,
            actions_applied=[],
        )
        self.rule.delete()
        log.refresh_from_db()
        self.assertIsNone(log.rule)
        self.assertEqual(log.rule_name_snapshot, "r1")
