"""Tests for workspace.mail.tasks Celery entry points.

The tasks delegate to workspace.mail.services.imap_sync.sync_account, which we
patch so the suite doesn't touch any real IMAP server.

sync_all_accounts only dispatches: it CAS-claims each due account by advancing
last_sync_at past the due threshold and enqueues a per-account worker. The
claim is what serializes overlapping runs, so most of the coverage here is
about that handshake rather than about the IMAP work itself.
"""

from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.mail import tasks as mail_tasks
from workspace.mail.models import MailAccount

User = get_user_model()


def _make_account(owner, *, email=None, is_active=True, last_sync_at=None):
    return MailAccount.objects.create(
        owner=owner,
        email=email or f"{owner.username}@example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username=owner.username,
        is_active=is_active,
        last_sync_at=last_sync_at,
    )


class SyncAllAccountsDispatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pass")
        cls.bob = User.objects.create_user(username="bob", password="pass")

    def test_dispatches_one_task_per_due_active_account(self):
        a1 = _make_account(self.alice)
        a2 = _make_account(self.bob)
        _make_account(self.alice, email="old@example.com", is_active=False)

        with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
            result = mail_tasks.sync_all_accounts.run()

        self.assertEqual(result["accounts_dispatched"], 2)
        dispatched = {call.args[0] for call in delay.call_args_list}
        self.assertEqual(dispatched, {str(a1.uuid), str(a2.uuid)})

    def test_never_synced_accounts_are_due(self):
        # last_sync_at IS NULL must not be excluded by the staleness filter,
        # otherwise a freshly added account would never sync.
        account = _make_account(self.alice, last_sync_at=None)

        with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
            mail_tasks.sync_all_accounts.run()

        self.assertEqual(
            [call.args[0] for call in delay.call_args_list], [str(account.uuid)]
        )

    def test_recently_synced_account_is_not_dispatched(self):
        _make_account(self.alice, last_sync_at=timezone.now())

        with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
            result = mail_tasks.sync_all_accounts.run()

        delay.assert_not_called()
        self.assertEqual(result["accounts_dispatched"], 0)

    @override_settings(MAIL_SYNC_INTERVAL=300)
    def test_account_synced_longer_ago_than_the_interval_is_dispatched(self):
        account = _make_account(
            self.alice, last_sync_at=timezone.now() - timedelta(seconds=600)
        )

        with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
            mail_tasks.sync_all_accounts.run()

        self.assertEqual(
            [call.args[0] for call in delay.call_args_list], [str(account.uuid)]
        )

    def test_inactive_account_is_not_dispatched(self):
        _make_account(self.alice, is_active=False)

        with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
            mail_tasks.sync_all_accounts.run()

        delay.assert_not_called()

    def test_claim_advances_last_sync_at_so_a_second_pass_is_a_no_op(self):
        # This is the overlap guard: the first dispatcher parks the account at
        # a future token, so a concurrent or immediately following pass no
        # longer sees it as due and cannot enqueue a duplicate IMAP sync.
        _make_account(self.alice)

        with mock.patch.object(mail_tasks.sync_single_account, "delay") as first:
            mail_tasks.sync_all_accounts.run()
        with mock.patch.object(mail_tasks.sync_single_account, "delay") as second:
            result = mail_tasks.sync_all_accounts.run()

        self.assertEqual(first.call_count, 1)
        second.assert_not_called()
        self.assertEqual(result["accounts_dispatched"], 0)

    def test_claim_token_is_passed_to_the_worker(self):
        account = _make_account(self.alice)

        with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
            mail_tasks.sync_all_accounts.run()

        account.refresh_from_db()
        token = delay.call_args.args[1]
        self.assertEqual(token, account.last_sync_at.isoformat())

    def test_broker_failure_rolls_the_claim_back(self):
        # Without the rollback the account would stay parked at the token for
        # the whole lock horizon and silently stop syncing.
        account = _make_account(self.alice, last_sync_at=None)

        with mock.patch.object(
            mail_tasks.sync_single_account, "delay", side_effect=RuntimeError("down")
        ):
            result = mail_tasks.sync_all_accounts.run()

        account.refresh_from_db()
        self.assertIsNone(account.last_sync_at)
        self.assertEqual(result["accounts_dispatched"], 0)

    def test_broker_failure_for_one_account_does_not_abort_the_others(self):
        a1 = _make_account(self.alice)
        _make_account(self.bob)

        def _flaky(account_uuid, *args):
            if account_uuid == str(a1.uuid):
                raise RuntimeError("down")

        with mock.patch.object(
            mail_tasks.sync_single_account, "delay", side_effect=_flaky
        ) as delay:
            result = mail_tasks.sync_all_accounts.run()

        self.assertEqual(delay.call_count, 2)
        self.assertEqual(result["accounts_dispatched"], 1)

    @override_settings(MAIL_SYNC_INTERVAL=300)
    def test_claim_parks_the_account_for_a_horizon_scaled_to_the_interval(self):
        # A claim that is never finalised (task enqueued, no worker ever ran
        # it) keeps the account from syncing until the horizon elapses. The
        # shared 1h default is sized for a 900s cadence; against 300s it would
        # turn a dropped message into an hour of silence.
        account = _make_account(self.alice)

        with mock.patch.object(mail_tasks.sync_single_account, "delay"):
            mail_tasks.sync_all_accounts.run()

        account.refresh_from_db()
        parked_for = account.last_sync_at - timezone.now()
        self.assertLess(
            parked_for,
            timedelta(minutes=30),
            "a dropped message must not silence the account for the shared 1h "
            f"default; this claim parked it for {parked_for}",
        )
        self.assertGreater(
            parked_for,
            timedelta(seconds=300),
            "the horizon must still exceed one interval, otherwise the row "
            "becomes due again while its worker is only just starting",
        )

    def test_losing_the_claim_race_does_not_enqueue(self):
        # The due filter normally hides this branch: it only fires when a
        # competing dispatcher claims the row between our SELECT and UPDATE.
        # Forcing cas_claim to lose is the only way to reach it.
        _make_account(self.alice)

        with mock.patch("workspace.mail.tasks.cas_claim", return_value=None):
            with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
                result = mail_tasks.sync_all_accounts.run()

        delay.assert_not_called()
        self.assertEqual(result["accounts_dispatched"], 0)
        self.assertEqual(result["already_claimed"], 1)

    def test_empty_account_list_returns_zero(self):
        with mock.patch.object(mail_tasks.sync_single_account, "delay") as delay:
            result = mail_tasks.sync_all_accounts.run()

        self.assertEqual(result["accounts_dispatched"], 0)
        delay.assert_not_called()


class SyncSingleAccountTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="pass")

    def test_not_found_when_account_missing(self):
        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            result = mail_tasks.sync_single_account.run(account_uuid=str(uuid4()))

        self.assertEqual(result, {"status": "not_found"})
        sync_mock.assert_not_called()

    def test_not_found_when_account_is_inactive(self):
        account = _make_account(self.alice, is_active=False)
        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            result = mail_tasks.sync_single_account.run(account_uuid=str(account.uuid))

        self.assertEqual(result, {"status": "not_found"})
        sync_mock.assert_not_called()

    def test_happy_path(self):
        account = _make_account(self.alice)
        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            result = mail_tasks.sync_single_account.run(account_uuid=str(account.uuid))

        sync_mock.assert_called_once()
        self.assertEqual(result, {"status": "ok", "email": account.email})

    def test_sync_failure_records_error_on_account(self):
        account = _make_account(self.alice)
        with mock.patch(
            "workspace.mail.services.imap_sync.sync_account",
            side_effect=RuntimeError("bad credentials"),
        ):
            result = mail_tasks.sync_single_account.run(account_uuid=str(account.uuid))

        self.assertEqual(result, {"status": "error", "error": "bad credentials"})
        account.refresh_from_db()
        self.assertEqual(account.last_sync_error, "bad credentials")

    def test_owner_is_fetched_with_the_account(self):
        # The sync path reads account.owner to gate the per-user AI features;
        # without select_related that is an extra query per account.
        account = _make_account(self.alice)

        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            mail_tasks.sync_single_account.run(str(account.uuid))

        passed = sync_mock.call_args.args[0]
        with self.assertNumQueries(0):
            _ = passed.owner

    def test_valid_claim_token_is_finalised_and_the_sync_runs(self):
        token = timezone.now() + timedelta(hours=1)
        account = _make_account(self.alice, last_sync_at=token)

        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            result = mail_tasks.sync_single_account.run(
                str(account.uuid), token.isoformat()
            )

        sync_mock.assert_called_once()
        self.assertEqual(result["status"], "ok")
        # Finalising replaces the future token with the real sync time,
        # otherwise the account would look "synced an hour from now" and stay
        # undue for that long.
        account.refresh_from_db()
        self.assertLess(account.last_sync_at, token)

    def test_duplicate_delivery_does_not_sync_twice(self):
        # Celery can redeliver a message (worker killed before the ack). The
        # second delivery must lose its CAS and bail before opening IMAP.
        token = timezone.now() + timedelta(hours=1)
        account = _make_account(self.alice, last_sync_at=token)

        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            first = mail_tasks.sync_single_account.run(
                str(account.uuid), token.isoformat()
            )
            second = mail_tasks.sync_single_account.run(
                str(account.uuid), token.isoformat()
            )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "already_claimed")
        sync_mock.assert_called_once()

    def test_stale_claim_token_is_refused(self):
        account = _make_account(self.alice, last_sync_at=timezone.now())
        stale = (timezone.now() + timedelta(days=1)).isoformat()

        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            result = mail_tasks.sync_single_account.run(str(account.uuid), stale)

        self.assertEqual(result["status"], "skipped")
        sync_mock.assert_not_called()

    def test_manual_call_without_a_token_skips_the_cas(self):
        # `mail.sync_account` is a named task, so it can be invoked by hand
        # (celery call, shell, a future caller). Those invocations have no
        # dispatcher window to pin against and must not be gated on a claim.
        account = _make_account(self.alice, last_sync_at=timezone.now())

        with mock.patch("workspace.mail.services.imap_sync.sync_account") as sync_mock:
            result = mail_tasks.sync_single_account.run(str(account.uuid))

        sync_mock.assert_called_once()
        self.assertEqual(result["status"], "ok")

    def test_malicious_email_cannot_forge_log_lines(self):
        # Account emails are user-supplied and reach the logger on the failure
        # path, so they must be flattened first (CWE-117 log injection).
        account = _make_account(
            self.alice, email="evil\r\nINFO:root:forged admin login@example.com"
        )

        with mock.patch(
            "workspace.mail.services.imap_sync.sync_account",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertLogs("workspace.mail.tasks", level="INFO") as cm:
                mail_tasks.sync_single_account.run(str(account.uuid))

        # Assert on the interpolated message, not on cm.output: this is
        # logger.exception, so the emitted record legitimately carries a
        # multi-line traceback that would mask what we're checking.
        messages = [
            r.getMessage() for r in cm.records if "Failed to sync account" in r.msg
        ]
        self.assertEqual(len(messages), 1)
        self.assertNotIn("\r", messages[0])
        self.assertNotIn("\n", messages[0])
        # The address content survives, flattened onto one line.
        self.assertIn("forged admin login", messages[0])
