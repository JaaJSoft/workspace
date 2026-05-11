"""Unit tests for workspace.common.celery_claim.

Each helper is exercised against real database rows (using ExternalCalendar
and ScheduledMessage) so the ORM filter semantics — NULL handling,
extra_where predicates, update-count checks — are verified against the
actual database rather than mocks.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar
from workspace.calendar.models_external import ExternalCalendar
from workspace.common.celery_claim import (
    DISPATCH_LOCK_HORIZON,
    cas_claim,
    cas_finalize,
    cas_rollback,
)

User = get_user_model()


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_ext(user, url='https://example.com/feed.ics', last_synced_at=None):
    """Create a Calendar + ExternalCalendar owned by user."""
    cal = Calendar.objects.create(name='Test Cal', owner=user)
    ext = ExternalCalendar.objects.create(calendar=cal, url=url)
    if last_synced_at is not None:
        ExternalCalendar.objects.filter(pk=ext.pk).update(last_synced_at=last_synced_at)
        ext.refresh_from_db()
    return ext


# ── DISPATCH_LOCK_HORIZON ─────────────────────────────────────────────────────


class DispatchLockHorizonTests(TestCase):
    def test_is_one_hour(self):
        self.assertEqual(DISPATCH_LOCK_HORIZON, timedelta(hours=1))


# ── cas_claim ────────────────────────────────────────────────────────────────


class CasClaimTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='claim_user', password='pass123',
        )

    def test_returns_future_token_on_success(self):
        ext = _make_ext(self.user)
        before = timezone.now()
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        after = timezone.now()

        self.assertIsNotNone(token)
        # Token should be approximately now + DISPATCH_LOCK_HORIZON.
        self.assertGreater(token, before + DISPATCH_LOCK_HORIZON - timedelta(seconds=2))
        self.assertLess(token, after + DISPATCH_LOCK_HORIZON + timedelta(seconds=2))

    def test_claim_writes_token_to_db(self):
        ext = _make_ext(self.user)
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        ext.refresh_from_db()
        self.assertEqual(ext.last_synced_at, token)

    def test_returns_none_when_value_already_changed(self):
        """A concurrent dispatcher already changed the field — we lose the race."""
        now = timezone.now()
        ext = _make_ext(self.user, last_synced_at=now - timedelta(minutes=5))
        # Simulate the other dispatcher advancing the field.
        advanced = now + timedelta(hours=2)
        ExternalCalendar.objects.filter(pk=ext.pk).update(last_synced_at=advanced)

        # Our claim is keyed on the pre-advance value — should fail.
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at',
            observed_value=now - timedelta(minutes=5),
        )
        self.assertIsNone(token)

    def test_null_observed_value_uses_isnull_predicate(self):
        """None observed_value must use __isnull=True, not __exact=None."""
        ext = _make_ext(self.user)  # last_synced_at starts as NULL
        # Give the row a non-null value to prove the isnull path is taken.
        ExternalCalendar.objects.filter(pk=ext.pk).update(
            last_synced_at=timezone.now() - timedelta(hours=1),
        )
        # Claiming with observed_value=None should fail because the DB row is
        # no longer NULL.
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        self.assertIsNone(token)

    def test_null_observed_value_succeeds_when_field_is_null(self):
        ext = _make_ext(self.user)  # last_synced_at is NULL
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        self.assertIsNotNone(token)

    def test_extra_where_prevents_claim_when_not_matching(self):
        """extra_where={'is_active': True} blocks the claim for inactive rows."""
        ext = _make_ext(self.user)
        ExternalCalendar.objects.filter(pk=ext.pk).update(is_active=False)

        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
            extra_where={'is_active': True},
        )
        self.assertIsNone(token)

    def test_extra_where_allows_claim_when_matching(self):
        ext = _make_ext(self.user)  # is_active defaults to True
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
            extra_where={'is_active': True},
        )
        self.assertIsNotNone(token)

    def test_custom_lock_horizon_is_used(self):
        ext = _make_ext(self.user)
        short_horizon = timedelta(minutes=30)
        before = timezone.now()
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
            lock_horizon=short_horizon,
        )
        after = timezone.now()

        self.assertIsNotNone(token)
        self.assertGreater(token, before + short_horizon - timedelta(seconds=2))
        self.assertLess(token, after + short_horizon + timedelta(seconds=2))

    def test_claim_on_non_null_observed_value(self):
        """cas_claim with a non-null observed_value uses __exact filter."""
        past = timezone.now() - timedelta(hours=2)
        ext = _make_ext(self.user, last_synced_at=past)

        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=past,
        )
        self.assertIsNotNone(token)
        ext.refresh_from_db()
        self.assertEqual(ext.last_synced_at, token)

    def test_double_claim_second_returns_none(self):
        """Two concurrent callers trying to claim the same NULL row: only one wins."""
        ext = _make_ext(self.user)

        token_a = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        # Second caller also observed NULL originally.
        token_b = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        self.assertIsNotNone(token_a)
        self.assertIsNone(token_b)


# ── cas_rollback ─────────────────────────────────────────────────────────────


class CasRollbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='rollback_user', password='pass123',
        )

    def test_restores_original_non_null_value(self):
        past = timezone.now() - timedelta(hours=3)
        ext = _make_ext(self.user, last_synced_at=past)
        # Dispatcher claimed the row, parking it at a future token.
        claim_token = timezone.now() + DISPATCH_LOCK_HORIZON
        ExternalCalendar.objects.filter(pk=ext.pk).update(
            last_synced_at=claim_token,
        )

        cas_rollback(ExternalCalendar, ext.pk, 'last_synced_at', past)

        ext.refresh_from_db()
        self.assertEqual(ext.last_synced_at, past)

    def test_restores_null_original_value(self):
        ext = _make_ext(self.user)  # last_synced_at starts NULL
        claim_token = timezone.now() + DISPATCH_LOCK_HORIZON
        ExternalCalendar.objects.filter(pk=ext.pk).update(
            last_synced_at=claim_token,
        )

        cas_rollback(ExternalCalendar, ext.pk, 'last_synced_at', None)

        ext.refresh_from_db()
        self.assertIsNone(ext.last_synced_at)

    def test_rollback_is_unconditional(self):
        """cas_rollback does not check extra_where — it always overwrites."""
        ext = _make_ext(self.user)
        ExternalCalendar.objects.filter(pk=ext.pk).update(is_active=False)
        claim_token = timezone.now() + DISPATCH_LOCK_HORIZON
        ExternalCalendar.objects.filter(pk=ext.pk).update(
            last_synced_at=claim_token,
        )

        # Even though is_active=False, rollback must still succeed.
        past = timezone.now() - timedelta(hours=1)
        cas_rollback(ExternalCalendar, ext.pk, 'last_synced_at', past)

        ext.refresh_from_db()
        self.assertEqual(ext.last_synced_at, past)


# ── cas_finalize ─────────────────────────────────────────────────────────────


class CasFinalizeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='finalize_user', password='pass123',
        )

    def _claimed_ext(self):
        """Return an ExternalCalendar whose last_synced_at is the claim token."""
        ext = _make_ext(self.user)
        self.claim_token = timezone.now() + DISPATCH_LOCK_HORIZON
        ExternalCalendar.objects.filter(pk=ext.pk).update(
            last_synced_at=self.claim_token,
        )
        ext.refresh_from_db()
        return ext

    # --- success paths -------------------------------------------------------

    def test_returns_true_and_applies_updates_with_datetime_token(self):
        ext = self._claimed_ext()
        new_ts = timezone.now()
        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=self.claim_token,
            updates={'last_synced_at': new_ts},
        )
        self.assertTrue(result)
        ext.refresh_from_db()
        # Allow a small delta because the DB may truncate microseconds.
        self.assertAlmostEqual(
            ext.last_synced_at.timestamp(), new_ts.timestamp(), delta=1,
        )

    def test_returns_true_with_iso_string_token(self):
        ext = self._claimed_ext()
        new_ts = timezone.now()
        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at',
            claim_token=self.claim_token.isoformat(),
            updates={'last_synced_at': new_ts},
        )
        self.assertTrue(result)

    def test_iso_string_and_datetime_token_are_equivalent(self):
        """Passing the ISO string vs the datetime object must produce the same result."""
        ext_a = self._claimed_ext()
        token_a = self.claim_token

        ext_b = _make_ext(self.user, url='https://example.com/b.ics')
        token_b = timezone.now() + DISPATCH_LOCK_HORIZON
        ExternalCalendar.objects.filter(pk=ext_b.pk).update(
            last_synced_at=token_b,
        )

        new_ts = timezone.now()
        result_a = cas_finalize(
            ExternalCalendar, ext_a.pk,
            claim_field='last_synced_at', claim_token=token_a,
            updates={'last_synced_at': new_ts},
        )
        result_b = cas_finalize(
            ExternalCalendar, ext_b.pk,
            claim_field='last_synced_at', claim_token=token_b.isoformat(),
            updates={'last_synced_at': new_ts},
        )
        self.assertTrue(result_a)
        self.assertTrue(result_b)

    # --- failure paths -------------------------------------------------------

    def test_returns_false_when_token_does_not_match(self):
        """Stale delivery: the row has been finalized by another worker."""
        ext = self._claimed_ext()
        stale_token = timezone.now() - timedelta(hours=2)  # wrong token

        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=stale_token,
            updates={'last_synced_at': timezone.now()},
        )
        self.assertFalse(result)

    def test_returns_false_when_extra_where_not_satisfied(self):
        """Row is inactive — extra_where={'is_active': True} must block the update."""
        ext = self._claimed_ext()
        ExternalCalendar.objects.filter(pk=ext.pk).update(is_active=False)

        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=self.claim_token,
            updates={'last_synced_at': timezone.now()},
            extra_where={'is_active': True},
        )
        self.assertFalse(result)

    def test_updates_not_applied_on_failure(self):
        """When CAS fails, the updates dict must not be written to the row."""
        ext = self._claimed_ext()
        sentinel = timezone.now() - timedelta(days=999)
        stale_token = timezone.now() - timedelta(hours=2)

        cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=stale_token,
            updates={'last_synced_at': sentinel},
        )
        ext.refresh_from_db()
        # Row should still hold the claim token, not the sentinel.
        self.assertAlmostEqual(
            ext.last_synced_at.timestamp(),
            self.claim_token.timestamp(),
            delta=1,
        )

    # --- None claim_token (legacy / manual-trigger path) ---------------------

    def test_none_token_skips_cas_and_always_applies_updates(self):
        """claim_token=None bypasses the CAS predicate — any row matching pk
        (and extra_where if supplied) gets updated."""
        ext = _make_ext(self.user)
        new_ts = timezone.now()

        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=None,
            updates={'last_synced_at': new_ts},
        )
        self.assertTrue(result)
        ext.refresh_from_db()
        self.assertAlmostEqual(
            ext.last_synced_at.timestamp(), new_ts.timestamp(), delta=1,
        )

    def test_none_token_still_respects_extra_where(self):
        """Even without a token, the extra_where predicate is enforced."""
        ext = _make_ext(self.user)
        ExternalCalendar.objects.filter(pk=ext.pk).update(is_active=False)
        new_ts = timezone.now()

        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=None,
            updates={'last_synced_at': new_ts},
            extra_where={'is_active': True},
        )
        self.assertFalse(result)
        ext.refresh_from_db()
        self.assertIsNone(ext.last_synced_at)

    def test_none_token_applies_multiple_updates(self):
        """updates dict with multiple fields are all written on success."""
        ext = _make_ext(self.user)
        ExternalCalendar.objects.filter(pk=ext.pk).update(last_error='old error')

        new_ts = timezone.now()
        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=None,
            updates={'last_synced_at': new_ts, 'last_error': ''},
        )
        self.assertTrue(result)
        ext.refresh_from_db()
        self.assertEqual(ext.last_error, '')

    # --- idempotency & duplicate delivery ------------------------------------

    def test_second_finalize_with_same_token_returns_false(self):
        """Simulates a duplicate Celery delivery: first worker already
        finalised the claim, second worker presents the same token but
        the field has moved on — CAS returns False."""
        ext = self._claimed_ext()
        new_ts = timezone.now()

        # First worker finalises successfully.
        result_first = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=self.claim_token,
            updates={'last_synced_at': new_ts},
        )
        self.assertTrue(result_first)

        # Second worker (duplicate delivery) tries with the same token.
        result_second = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=self.claim_token,
            updates={'last_synced_at': timezone.now() + timedelta(hours=10)},
        )
        self.assertFalse(result_second)

        # Row should reflect first worker's update, not second's.
        ext.refresh_from_db()
        self.assertAlmostEqual(
            ext.last_synced_at.timestamp(), new_ts.timestamp(), delta=1,
        )


# ── Round-trip: claim → finalize ─────────────────────────────────────────────


class CasRoundTripTests(TestCase):
    """Integration-style tests that exercise the full dispatcher/worker pattern."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='roundtrip_user', password='pass123',
        )

    def test_dispatcher_claim_then_worker_finalize(self):
        ext = _make_ext(self.user)  # last_synced_at=NULL

        # Dispatcher claims the row.
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
            extra_where={'is_active': True},
        )
        self.assertIsNotNone(token)

        # Worker finalizes the claim.
        final_ts = timezone.now()
        result = cas_finalize(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', claim_token=token.isoformat(),
            updates={'last_synced_at': final_ts},
            extra_where={'is_active': True},
        )
        self.assertTrue(result)

        ext.refresh_from_db()
        self.assertAlmostEqual(
            ext.last_synced_at.timestamp(), final_ts.timestamp(), delta=1,
        )

    def test_dispatcher_claim_rollback_then_reclaim(self):
        ext = _make_ext(self.user)  # last_synced_at=NULL

        # Dispatcher claims, then broker fails, dispatcher rolls back.
        token = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        self.assertIsNotNone(token)
        cas_rollback(ExternalCalendar, ext.pk, 'last_synced_at', None)

        ext.refresh_from_db()
        self.assertIsNone(ext.last_synced_at)

        # On the next dispatcher pass, the row is claimable again.
        token2 = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        self.assertIsNotNone(token2)

    def test_concurrent_dispatcher_only_one_wins(self):
        """Two concurrent dispatchers racing on the same NULL row: exactly
        one gets a token and one gets None."""
        ext = _make_ext(self.user)

        token_a = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        token_b = cas_claim(
            ExternalCalendar, ext.pk,
            claim_field='last_synced_at', observed_value=None,
        )
        results = [token_a, token_b]
        self.assertEqual(results.count(None), 1)
        self.assertEqual(sum(1 for t in results if t is not None), 1)
