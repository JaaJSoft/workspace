from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.rules.conditions import evaluate_node
from workspace.mail.services.rules.schema import parse_conditions

User = get_user_model()


def _node(field, op, value=None, case_sensitive=False):
    d = {'field': field, 'op': op, 'case_sensitive': case_sensitive}
    if value is not None:
        d['value'] = value
    return parse_conditions(d)


class TextConditionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cu', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='cu@x.com',
            imap_host='x', smtp_host='x', username='cu@x.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='Inbox',
            display_name='Inbox', folder_type='inbox',
        )
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            subject='Quarterly review with Alice',
            from_address={'name': 'Alice', 'email': 'alice@github.com'},
            to_addresses=[{'name': 'Bob', 'email': 'bob@team-x.com'}],
            cc_addresses=[{'name': 'Eve', 'email': 'eve@team-x.com'}],
            body_text='Please find attached the report. Thanks.',
            date=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

    def test_from_contains_match(self):
        self.assertTrue(evaluate_node(_node('from', 'contains', '@github.com'), self.msg))

    def test_from_contains_no_match(self):
        self.assertFalse(evaluate_node(_node('from', 'contains', '@gitlab.com'), self.msg))

    def test_from_matches_name_or_email(self):
        self.assertTrue(evaluate_node(_node('from', 'contains', 'Alice'), self.msg))

    def test_subject_starts_with(self):
        self.assertTrue(evaluate_node(_node('subject', 'starts_with', 'Quarter'), self.msg))
        self.assertFalse(evaluate_node(_node('subject', 'starts_with', 'review'), self.msg))

    def test_subject_ends_with(self):
        self.assertTrue(evaluate_node(_node('subject', 'ends_with', 'Alice'), self.msg))

    def test_subject_equals_case_insensitive(self):
        self.assertTrue(evaluate_node(
            _node('subject', 'equals', 'QUARTERLY REVIEW WITH ALICE'), self.msg,
        ))

    def test_subject_equals_case_sensitive_false(self):
        self.assertFalse(evaluate_node(
            _node('subject', 'equals', 'QUARTERLY REVIEW WITH ALICE', case_sensitive=True),
            self.msg,
        ))

    def test_recipient_matches_to_or_cc(self):
        self.assertTrue(evaluate_node(_node('recipient', 'contains', 'bob@'), self.msg))
        self.assertTrue(evaluate_node(_node('recipient', 'contains', 'eve@'), self.msg))

    def test_body_contains(self):
        self.assertTrue(evaluate_node(_node('body', 'contains', 'report'), self.msg))

    def test_folder_equals(self):
        self.assertTrue(evaluate_node(_node('folder', 'equals', 'Inbox'), self.msg))

    def test_in_list_match_any(self):
        self.assertTrue(evaluate_node(
            _node('from', 'in_list', ['noreply@x.com', 'alice@github.com']),
            self.msg,
        ))
        self.assertFalse(evaluate_node(
            _node('from', 'in_list', ['noreply@x.com']),
            self.msg,
        ))
