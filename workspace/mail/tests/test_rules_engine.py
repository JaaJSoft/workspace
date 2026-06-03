from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import (
    MailAccount, MailFolder, MailMessage, MailMessageLabel, MailRule, MailRuleLog,
)
from workspace.mail.services.rules.engine import (
    apply_rule_to_folder,
    run_rules_for_messages,
)

User = get_user_model()


class EngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='eu', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='e@x.com',
            imap_host='x', smtp_host='x', username='e@x.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.label = self.account.labels.first()  # auto-seeded
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            from_address={'name': '', 'email': 'newsletter@news.com'},
            subject='Daily news',
        )

    def _rule(self, **kw):
        kw.setdefault('account', self.account)
        kw.setdefault('name', 'r')
        kw.setdefault('conditions', {
            'field': 'from', 'op': 'contains', 'value': '@news.com',
        })
        kw.setdefault('actions', [
            {'type': 'add_label', 'label_id': str(self.label.uuid)},
        ])
        return MailRule.objects.create(**kw)

    def test_match_runs_action_and_writes_log(self):
        rule = self._rule()
        summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
        self.assertEqual(summary[str(self.msg.uuid)], [str(rule.uuid)])
        self.assertTrue(
            MailMessageLabel.objects.filter(message=self.msg, label=self.label).exists(),
        )
        log = MailRuleLog.objects.get(rule=rule, message=self.msg)
        self.assertEqual(log.rule_name_snapshot, rule.name)
        self.assertEqual(len(log.actions_applied), 1)
        rule.refresh_from_db()
        self.assertEqual(rule.match_count, 1)
        self.assertIsNotNone(rule.last_matched_at)

    def test_disabled_rule_skipped(self):
        self._rule(is_enabled=False)
        summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
        self.assertEqual(summary, {})
        self.assertEqual(MailRuleLog.objects.count(), 0)

    def test_no_match_no_log(self):
        self._rule(conditions={'field': 'from', 'op': 'contains', 'value': '@other.com'})
        summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
        self.assertEqual(summary, {})

    def test_position_ordering(self):
        # Rule with higher position runs LATER (position 0 first).
        first = self._rule(name='first', position=0)
        second = self._rule(name='second', position=1)
        summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
        self.assertEqual(summary[str(self.msg.uuid)], [str(first.uuid), str(second.uuid)])

    def test_stop_processing_halts_chain(self):
        first = self._rule(name='first', position=0, stop_processing=True)
        self._rule(name='second', position=1)
        summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
        self.assertEqual(summary[str(self.msg.uuid)], [str(first.uuid)])

    def test_delete_action_halts_chain_for_message(self):
        with patch('workspace.mail.services.rules.actions.delete_message'):
            self._rule(name='delrule', position=0, actions=[{'type': 'delete'}])
            self._rule(name='afterdel', position=1)
            summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
            self.assertEqual(len(summary[str(self.msg.uuid)]), 1)

    def test_rule_failure_does_not_break_other_rules(self):
        # First rule has a valid action but we'll force it to crash; second rule still runs.
        bad = self._rule(
            name='bad', position=0,
            actions=[{'type': 'add_label', 'label_id': '00000000-0000-0000-0000-000000000000'}],
        )
        good = self._rule(name='good', position=1)
        summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
        # The 'bad' rule "matched" (conditions match) but its action failed.
        # Engine still records both rules as having matched.
        self.assertEqual(summary[str(self.msg.uuid)], [str(bad.uuid), str(good.uuid)])

    def test_no_messages_no_rules_returns_empty(self):
        self.assertEqual(run_rules_for_messages(self.account, []), {})
        self.assertEqual(run_rules_for_messages(self.account, [str(self.msg.uuid)]), {})

    def test_other_account_rule_does_not_apply(self):
        other = User.objects.create_user(username='oth', password='p')
        other_account = MailAccount.objects.create(
            owner=other, email='o@x.com',
            imap_host='x', smtp_host='x', username='o@x.com',
        )
        MailRule.objects.create(
            account=other_account, name='leak',
            conditions={'field': 'from', 'op': 'contains', 'value': '@news.com'},
            actions=[{'type': 'mark_read'}],
        )
        summary = run_rules_for_messages(self.account, [str(self.msg.uuid)])
        self.assertEqual(summary, {})


class ApplyRuleToFolderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='af', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='a@x.com',
            imap_host='x', smtp_host='x', username='a@x.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.label = self.account.labels.first()

    def _msg(self, **kw):
        kw.setdefault('account', self.account)
        kw.setdefault('folder', self.folder)
        kw.setdefault('from_address', {'name': '', 'email': 'newsletter@news.com'})
        kw.setdefault('subject', 'Daily news')
        return MailMessage.objects.create(**kw)

    def _rule(self, **kw):
        kw.setdefault('account', self.account)
        kw.setdefault('name', 'r')
        kw.setdefault('conditions', {'field': 'from', 'op': 'contains', 'value': '@news.com'})
        kw.setdefault('actions', [{'type': 'add_label', 'label_id': str(self.label.uuid)}])
        return MailRule.objects.create(**kw)

    def test_dry_run_counts_without_applying(self):
        self._msg(imap_uid=1)
        self._msg(imap_uid=2, from_address={'name': '', 'email': 'x@other.com'})
        rule = self._rule()
        result = apply_rule_to_folder(rule, self.folder, dry_run=True)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['applied'], 0)
        self.assertEqual(result['scanned'], 2)
        self.assertEqual(result['total'], 2)
        self.assertFalse(result['capped'])
        self.assertEqual(MailMessageLabel.objects.count(), 0)
        self.assertEqual(MailRuleLog.objects.count(), 0)
        rule.refresh_from_db()
        self.assertEqual(rule.match_count, 0)

    def test_real_apply_runs_actions_logs_and_stats(self):
        m = self._msg(imap_uid=1)
        rule = self._rule()
        result = apply_rule_to_folder(rule, self.folder, dry_run=False)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['applied'], 1)
        self.assertEqual(result['imap_failed'], 0)
        self.assertTrue(
            MailMessageLabel.objects.filter(message=m, label=self.label).exists()
        )
        self.assertEqual(MailRuleLog.objects.filter(rule=rule, message=m).count(), 1)
        rule.refresh_from_db()
        self.assertEqual(rule.match_count, 1)
        self.assertIsNotNone(rule.last_matched_at)

    def test_cap_limits_scan(self):
        for i in range(3):
            self._msg(imap_uid=i + 1)
        rule = self._rule()
        result = apply_rule_to_folder(rule, self.folder, dry_run=True, limit=2)
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['scanned'], 2)
        self.assertTrue(result['capped'])

    def test_disabled_rule_still_applies(self):
        self._msg(imap_uid=1)
        rule = self._rule(is_enabled=False)
        result = apply_rule_to_folder(rule, self.folder, dry_run=False)
        self.assertEqual(result['applied'], 1)

    def test_imap_failure_counted(self):
        self._msg(imap_uid=1)
        target = MailFolder.objects.create(
            account=self.account, name='Archive',
            display_name='Archive', folder_type='archive',
        )
        rule = self._rule(actions=[{'type': 'move_to_folder', 'folder_id': str(target.uuid)}])
        with patch('workspace.mail.services.rules.actions.move_message', side_effect=Exception('boom')):
            result = apply_rule_to_folder(rule, self.folder, dry_run=False)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['imap_failed'], 1)
