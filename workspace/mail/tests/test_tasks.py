"""Tests for workspace.mail.tasks Celery entry points.

The tasks delegate to workspace.mail.services.imap.sync_account, which we
patch so the suite doesn't touch any real IMAP server.
"""

from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail import tasks as mail_tasks
from workspace.mail.models import MailAccount

User = get_user_model()


def _make_account(owner, *, email=None, is_active=True):
    return MailAccount.objects.create(
        owner=owner,
        email=email or f'{owner.username}@example.com',
        imap_host='imap.example.com',
        smtp_host='smtp.example.com',
        username=owner.username,
        is_active=is_active,
    )


class SyncAllAccountsTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='pass')
        cls.bob = User.objects.create_user(username='bob', password='pass')

    def test_syncs_every_active_account(self):
        a1 = _make_account(self.alice)
        a2 = _make_account(self.bob)
        _make_account(self.alice, email='old@example.com', is_active=False)

        with mock.patch(
            'workspace.mail.services.imap.sync_account'
        ) as sync_mock:
            result = mail_tasks.sync_all_accounts.run()

        self.assertEqual(result, {'synced': 2, 'errors': 0})
        synced_emails = {c.args[0].email for c in sync_mock.call_args_list}
        self.assertEqual(synced_emails, {a1.email, a2.email})

    def test_counts_errors_and_records_them(self):
        a1 = _make_account(self.alice)
        a2 = _make_account(self.bob)

        def _fake_sync(account):
            if account.pk == a1.pk:
                raise RuntimeError('IMAP offline')

        with mock.patch(
            'workspace.mail.services.imap.sync_account',
            side_effect=_fake_sync,
        ):
            result = mail_tasks.sync_all_accounts.run()

        self.assertEqual(result, {'synced': 1, 'errors': 1})
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(a1.last_sync_error, 'IMAP offline')
        self.assertEqual(a2.last_sync_error, '')

    def test_empty_account_list_returns_zero(self):
        with mock.patch(
            'workspace.mail.services.imap.sync_account'
        ) as sync_mock:
            result = mail_tasks.sync_all_accounts.run()

        self.assertEqual(result, {'synced': 0, 'errors': 0})
        sync_mock.assert_not_called()


class SyncSingleAccountTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='pass')

    def test_not_found_when_account_missing(self):
        with mock.patch(
            'workspace.mail.services.imap.sync_account'
        ) as sync_mock:
            result = mail_tasks.sync_single_account.run(account_uuid=str(uuid4()))

        self.assertEqual(result, {'status': 'not_found'})
        sync_mock.assert_not_called()

    def test_not_found_when_account_is_inactive(self):
        account = _make_account(self.alice, is_active=False)
        with mock.patch(
            'workspace.mail.services.imap.sync_account'
        ) as sync_mock:
            result = mail_tasks.sync_single_account.run(account_uuid=str(account.uuid))

        self.assertEqual(result, {'status': 'not_found'})
        sync_mock.assert_not_called()

    def test_happy_path(self):
        account = _make_account(self.alice)
        with mock.patch(
            'workspace.mail.services.imap.sync_account'
        ) as sync_mock:
            result = mail_tasks.sync_single_account.run(account_uuid=str(account.uuid))

        sync_mock.assert_called_once()
        self.assertEqual(result, {'status': 'ok', 'email': account.email})

    def test_sync_failure_records_error_on_account(self):
        account = _make_account(self.alice)
        with mock.patch(
            'workspace.mail.services.imap.sync_account',
            side_effect=RuntimeError('bad credentials'),
        ):
            result = mail_tasks.sync_single_account.run(account_uuid=str(account.uuid))

        self.assertEqual(result, {'status': 'error', 'error': 'bad credentials'})
        account.refresh_from_db()
        self.assertEqual(account.last_sync_error, 'bad credentials')
