from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount
from workspace.mail.queries import user_account_ids

User = get_user_model()


class UserAccountIdsTests(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')

    def _make_account(self, owner, email=None):
        return MailAccount.objects.create(
            owner=owner,
            email=email or f'{owner.username}@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username=owner.username,
        )

    def test_returns_owned_accounts(self):
        acct = self._make_account(self.alice)
        ids = list(user_account_ids(self.alice))
        self.assertIn(acct.pk, ids)

    def test_excludes_other_users_accounts(self):
        self._make_account(self.bob)
        ids = list(user_account_ids(self.alice))
        self.assertEqual(ids, [])

    def test_returns_multiple_accounts(self):
        a1 = self._make_account(self.alice, 'alice@work.com')
        a2 = self._make_account(self.alice, 'alice@personal.com')
        ids = list(user_account_ids(self.alice))
        self.assertEqual(set(ids), {a1.pk, a2.pk})

    def test_returns_empty_for_user_with_no_accounts(self):
        carol = User.objects.create_user(username='carol', password='pass')
        ids = list(user_account_ids(carol))
        self.assertEqual(ids, [])
