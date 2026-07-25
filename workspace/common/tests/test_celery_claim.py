"""Tests for the CAS claim primitives and the dispatcher loop built on them.

These had no direct coverage: they were exercised only through the three
modules that use them, so a change here could break a caller without failing
anything nearby.

``User`` stands in as the claimed model. It has the required shape already -
a nullable datetime (``last_login``) to claim on and a boolean (``is_active``)
to gate on - and using it keeps this module's tests from depending on a
sibling app's schema.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.common.celery_claim import (
    DISPATCH_LOCK_HORIZON,
    cas_claim,
    cas_finalize,
    cas_rollback,
    dispatch_due,
)

User = get_user_model()


class CasClaimTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="claimed", password="p")

    def test_claims_a_row_whose_value_matches_and_returns_the_token(self):
        self.user.last_login = timezone.now() - timedelta(hours=2)
        self.user.save(update_fields=["last_login"])

        token = cas_claim(
            User, self.user.pk, "last_login", observed_value=self.user.last_login
        )

        self.assertIsNotNone(token)
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_login, token)
        self.assertGreater(token, timezone.now())

    def test_claims_a_never_claimed_row_via_the_isnull_branch(self):
        # observed_value=None must translate to `field IS NULL`, not to
        # `field = NULL`, which matches nothing in SQL.
        self.assertIsNone(self.user.last_login)

        token = cas_claim(User, self.user.pk, "last_login", observed_value=None)

        self.assertIsNotNone(token)
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_login, token)

    def test_returns_none_when_the_observed_value_is_stale(self):
        # This is the whole point: a competing dispatcher already moved the
        # field, so our UPDATE matches zero rows and we must not enqueue.
        stale = timezone.now() - timedelta(hours=2)
        self.user.last_login = timezone.now()
        self.user.save(update_fields=["last_login"])

        token = cas_claim(User, self.user.pk, "last_login", observed_value=stale)

        self.assertIsNone(token)

    def test_returns_none_when_extra_where_no_longer_matches(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        token = cas_claim(
            User,
            self.user.pk,
            "last_login",
            observed_value=None,
            extra_where={"is_active": True},
        )

        self.assertIsNone(token)

    def test_lock_horizon_controls_how_far_the_token_is_parked(self):
        token = cas_claim(
            User,
            self.user.pk,
            "last_login",
            observed_value=None,
            lock_horizon=timedelta(seconds=60),
        )

        self.assertLess(token - timezone.now(), timedelta(seconds=61))

    def test_default_horizon_is_the_shared_constant(self):
        token = cas_claim(User, self.user.pk, "last_login", observed_value=None)

        remaining = token - timezone.now()
        self.assertGreater(remaining, DISPATCH_LOCK_HORIZON - timedelta(minutes=1))
        self.assertLessEqual(remaining, DISPATCH_LOCK_HORIZON)

    def test_only_the_targeted_row_is_claimed(self):
        other = User.objects.create_user(username="bystander", password="p")

        cas_claim(User, self.user.pk, "last_login", observed_value=None)

        other.refresh_from_db()
        self.assertIsNone(other.last_login)


class CasRollbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rolled", password="p")

    def test_restores_the_previous_value(self):
        original = timezone.now() - timedelta(hours=3)
        self.user.last_login = original
        self.user.save(update_fields=["last_login"])
        cas_claim(User, self.user.pk, "last_login", observed_value=original)

        cas_rollback(User, self.user.pk, "last_login", original)

        self.user.refresh_from_db()
        self.assertEqual(self.user.last_login, original)

    def test_restores_null_for_a_never_claimed_row(self):
        cas_claim(User, self.user.pk, "last_login", observed_value=None)

        cas_rollback(User, self.user.pk, "last_login", None)

        self.user.refresh_from_db()
        self.assertIsNone(self.user.last_login)


class CasFinalizeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="finalized", password="p")

    def test_finalizes_against_the_matching_token(self):
        token = cas_claim(User, self.user.pk, "last_login", observed_value=None)
        settled = timezone.now()

        ok = cas_finalize(
            User, self.user.pk, "last_login", token, {"last_login": settled}
        )

        self.assertTrue(ok)
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_login, settled)

    def test_accepts_an_isoformat_token(self):
        # The token crosses a Celery message boundary as a string.
        token = cas_claim(User, self.user.pk, "last_login", observed_value=None)

        ok = cas_finalize(
            User,
            self.user.pk,
            "last_login",
            token.isoformat(),
            {"last_login": timezone.now()},
        )

        self.assertTrue(ok)

    def test_second_finalize_with_the_same_token_loses(self):
        # A redelivered Celery message must not run the side effect twice.
        token = cas_claim(User, self.user.pk, "last_login", observed_value=None)
        cas_finalize(
            User, self.user.pk, "last_login", token, {"last_login": timezone.now()}
        )

        again = cas_finalize(
            User, self.user.pk, "last_login", token, {"last_login": timezone.now()}
        )

        self.assertFalse(again)

    def test_none_token_skips_the_cas_predicate(self):
        # The manual / direct-call path: no dispatcher window to pin against.
        ok = cas_finalize(
            User, self.user.pk, "last_login", None, {"last_login": timezone.now()}
        )

        self.assertTrue(ok)

    def test_extra_where_still_gates_a_valid_token(self):
        token = cas_claim(User, self.user.pk, "last_login", observed_value=None)
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        ok = cas_finalize(
            User,
            self.user.pk,
            "last_login",
            token,
            {"last_login": timezone.now()},
            extra_where={"is_active": True},
        )

        self.assertFalse(ok)


class DispatchDueTests(TestCase):
    """The loop that was duplicated across calendar, ai and mail."""

    def setUp(self):
        # Data migrations seed users (the AI assistant bot from
        # ai.0002_create_default_bot when AI_API_KEY is configured), and they
        # match the due predicate below - active with a NULL last_login. Park
        # them so the dispatch counts reflect only what each test creates.
        User.objects.update(is_active=False)
        self.worker = mock.Mock()

    def _make(self, username, *, last_login=None, is_active=True):
        return User.objects.create_user(
            username=username, password="p", is_active=is_active, last_login=last_login
        )

    def _due(self):
        return User.objects.filter(is_active=True, last_login__isnull=True).only(
            "pk", "last_login"
        )

    def test_dispatches_one_task_per_due_row(self):
        a = self._make("a")
        b = self._make("b")

        outcome = dispatch_due(
            self._due(), self.worker, claim_field="last_login", label="thing"
        )

        self.assertEqual(outcome.dispatched, 2)
        self.assertEqual(outcome.already_claimed, 0)
        enqueued = {call.args[0] for call in self.worker.delay.call_args_list}
        self.assertEqual(enqueued, {str(a.pk), str(b.pk)})

    def test_passes_the_claim_token_as_the_second_argument(self):
        user = self._make("a")

        dispatch_due(self._due(), self.worker, claim_field="last_login")

        user.refresh_from_db()
        self.assertEqual(
            self.worker.delay.call_args.args[1], user.last_login.isoformat()
        )

    def test_claiming_removes_the_row_from_the_due_predicate(self):
        # The claim is the overlap guard: a following pass must find nothing.
        self._make("a")

        first = dispatch_due(self._due(), self.worker, claim_field="last_login")
        second = dispatch_due(self._due(), self.worker, claim_field="last_login")

        self.assertEqual(first.dispatched, 1)
        self.assertEqual(second.dispatched, 0)

    def test_rows_excluded_by_the_due_queryset_are_untouched(self):
        inactive = self._make("inactive", is_active=False)

        outcome = dispatch_due(self._due(), self.worker, claim_field="last_login")

        self.assertEqual(outcome.dispatched, 0)
        self.worker.delay.assert_not_called()
        inactive.refresh_from_db()
        self.assertIsNone(inactive.last_login)

    def test_broker_failure_rolls_that_rows_claim_back(self):
        user = self._make("a")
        self.worker.delay.side_effect = RuntimeError("broker down")

        outcome = dispatch_due(self._due(), self.worker, claim_field="last_login")

        self.assertEqual(outcome.dispatched, 0)
        user.refresh_from_db()
        self.assertIsNone(
            user.last_login,
            "a failed enqueue must leave the row due, not parked at the token",
        )

    def test_broker_failure_on_one_row_does_not_abort_the_rest(self):
        first = self._make("a")
        self._make("b")
        failing = str(first.pk)

        def _flaky(pk, token):
            if pk == failing:
                raise RuntimeError("broker down")

        self.worker.delay.side_effect = _flaky

        outcome = dispatch_due(self._due(), self.worker, claim_field="last_login")

        self.assertEqual(self.worker.delay.call_count, 2)
        self.assertEqual(outcome.dispatched, 1)

    def test_losing_the_claim_is_counted_not_dispatched(self):
        self._make("a")

        with mock.patch("workspace.common.celery_claim.cas_claim", return_value=None):
            outcome = dispatch_due(self._due(), self.worker, claim_field="last_login")

        self.worker.delay.assert_not_called()
        self.assertEqual(outcome.dispatched, 0)
        self.assertEqual(outcome.already_claimed, 1)

    def test_lock_horizon_is_forwarded_to_the_claim(self):
        user = self._make("a")

        dispatch_due(
            self._due(),
            self.worker,
            claim_field="last_login",
            lock_horizon=timedelta(seconds=90),
        )

        user.refresh_from_db()
        self.assertLess(user.last_login - timezone.now(), timedelta(seconds=91))

    def test_extra_where_is_forwarded_to_the_claim(self):
        # Deactivating between the SELECT and the UPDATE must lose the claim,
        # which is what extra_where is for.
        user = self._make("a")
        due = list(self._due())
        User.objects.filter(pk=user.pk).update(is_active=False)

        outcome = dispatch_due(
            due=User.objects.filter(pk__in=[u.pk for u in due]).only(
                "pk", "last_login"
            ),
            worker=self.worker,
            claim_field="last_login",
            extra_where={"is_active": True},
        )

        self.assertEqual(outcome.dispatched, 0)
        self.assertEqual(outcome.already_claimed, 1)

    def test_failure_is_logged_through_the_callers_logger(self):
        # Each module keeps its own logger so the message lands under its name.
        self._make("a")
        self.worker.delay.side_effect = RuntimeError("broker down")

        with self.assertLogs("workspace.common.tests", level="ERROR") as cm:
            import logging

            dispatch_due(
                self._due(),
                self.worker,
                claim_field="last_login",
                label="widget",
                log=logging.getLogger("workspace.common.tests"),
            )

        self.assertTrue(any("widget" in m for m in cm.output))

    def test_reading_the_claim_field_costs_no_extra_query(self):
        # The docstring tells callers to .only('pk', claim_field); if the loop
        # ever read something outside that, every row would fault a query.
        for name in ("a", "b", "c"):
            self._make(name)

        with self.assertNumQueries(1 + 3 * 1):
            # 1 SELECT for the queryset, 1 UPDATE (the claim) per row.
            dispatch_due(self._due(), self.worker, claim_field="last_login")
