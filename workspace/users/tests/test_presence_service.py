from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.users.models import UserPresence
from workspace.users import presence_service

User = get_user_model()


class PresenceTestMixin:
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')

    def tearDown(self):
        cache.clear()


# ── set_manual_status / get_manual_status ───────────────────────

class ManualStatusTests(PresenceTestMixin, TestCase):

    def test_get_defaults_to_auto_when_no_presence(self):
        self.assertEqual(presence_service.get_manual_status(self.user.pk), 'auto')

    def test_set_and_get_manual_status(self):
        presence_service.set_manual_status(self.user.pk, 'busy')
        self.assertEqual(presence_service.get_manual_status(self.user.pk), 'busy')

    def test_set_updates_db(self):
        UserPresence.objects.create(user=self.user, last_seen=timezone.now())
        presence_service.set_manual_status(self.user.pk, 'away')
        self.assertEqual(
            UserPresence.objects.get(user=self.user).manual_status, 'away',
        )

    def test_get_falls_back_to_db_when_cache_empty(self):
        UserPresence.objects.create(
            user=self.user, last_seen=timezone.now(), manual_status='busy',
        )
        # Cache is empty (cleared in setUp)
        self.assertEqual(presence_service.get_manual_status(self.user.pk), 'busy')

    def test_get_caches_after_db_fallback(self):
        UserPresence.objects.create(
            user=self.user, last_seen=timezone.now(), manual_status='away',
        )
        presence_service.get_manual_status(self.user.pk)
        # Second call should use cache, not DB
        with self.assertNumQueries(0):
            result = presence_service.get_manual_status(self.user.pk)
        self.assertEqual(result, 'away')


# ── touch ───────────────────────────────────────────────────────

class TouchTests(PresenceTestMixin, TestCase):

    def test_touch_sets_activity_cache(self):
        presence_service.touch(self.user.pk)
        raw = cache.get(f'presence:activity:{self.user.pk}')
        self.assertIsNotNone(raw)

    def test_touch_sets_public_cache_for_auto_status(self):
        presence_service.touch(self.user.pk)
        raw = cache.get(f'presence:{self.user.pk}')
        self.assertIsNotNone(raw)

    def test_touch_skips_public_cache_for_invisible(self):
        # Ensure a UserPresence row exists so _sync_db doesn't fail on create
        UserPresence.objects.create(user=self.user, last_seen=timezone.now())
        presence_service.set_manual_status(self.user.pk, 'invisible')
        cache.delete(f'presence:{self.user.pk}')
        # Clear dbsync throttle so touch actually syncs
        cache.delete(f'presence:dbsync:{self.user.pk}')
        presence_service.touch(self.user.pk)
        raw = cache.get(f'presence:{self.user.pk}')
        self.assertIsNone(raw)

    def test_touch_skips_public_cache_for_away(self):
        UserPresence.objects.create(user=self.user, last_seen=timezone.now())
        presence_service.set_manual_status(self.user.pk, 'away')
        cache.delete(f'presence:{self.user.pk}')
        cache.delete(f'presence:dbsync:{self.user.pk}')
        presence_service.touch(self.user.pk)
        raw = cache.get(f'presence:{self.user.pk}')
        self.assertIsNone(raw)

    def test_touch_syncs_to_db(self):
        presence_service.touch(self.user.pk)
        self.assertTrue(UserPresence.objects.filter(user=self.user).exists())

    def test_touch_throttles_db_sync(self):
        presence_service.touch(self.user.pk)
        initial_seen = UserPresence.objects.get(user=self.user).last_seen
        # Second touch should be throttled (dbsync key exists)
        presence_service.touch(self.user.pk)
        self.assertEqual(
            UserPresence.objects.get(user=self.user).last_seen, initial_seen,
        )


# ── get_status ──────────────────────────────────────────────────

class GetStatusTests(PresenceTestMixin, TestCase):

    def test_offline_when_no_data(self):
        self.assertEqual(presence_service.get_status(self.user.pk), 'offline')

    def test_online_when_recently_active(self):
        now = timezone.now()
        cache.set(f'presence:{self.user.pk}', now.isoformat(), 600)
        self.assertEqual(presence_service.get_status(self.user.pk), 'online')

    def test_away_when_idle(self):
        old = timezone.now() - timedelta(minutes=5)
        cache.set(f'presence:{self.user.pk}', old.isoformat(), 600)
        self.assertEqual(presence_service.get_status(self.user.pk), 'away')

    def test_offline_when_stale(self):
        old = timezone.now() - timedelta(minutes=15)
        cache.set(f'presence:{self.user.pk}', old.isoformat(), 600)
        self.assertEqual(presence_service.get_status(self.user.pk), 'offline')

    def test_manual_busy_overrides_auto(self):
        presence_service.set_manual_status(self.user.pk, 'busy')
        self.assertEqual(presence_service.get_status(self.user.pk), 'busy')

    def test_manual_away_overrides_auto(self):
        presence_service.set_manual_status(self.user.pk, 'away')
        self.assertEqual(presence_service.get_status(self.user.pk), 'away')

    def test_invisible_appears_offline(self):
        presence_service.set_manual_status(self.user.pk, 'invisible')
        self.assertEqual(presence_service.get_status(self.user.pk), 'offline')

    def test_manual_online_uses_auto_detection(self):
        """'online' manual status still uses activity timestamps."""
        presence_service.set_manual_status(self.user.pk, 'online')
        # No activity → offline
        self.assertEqual(presence_service.get_status(self.user.pk), 'offline')

    def test_corrupt_cache_value_returns_offline(self):
        cache.set(f'presence:{self.user.pk}', 'not-a-date', 600)
        self.assertEqual(presence_service.get_status(self.user.pk), 'offline')


# ── get_statuses (bulk) ─────────────────────────────────────────

class GetStatusesBulkTests(PresenceTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.bob = User.objects.create_user(username='bob', password='pass')

    def test_empty_list_returns_empty_dict(self):
        self.assertEqual(presence_service.get_statuses([]), {})

    def test_returns_statuses_for_multiple_users(self):
        now = timezone.now()
        cache.set(f'presence:{self.user.pk}', now.isoformat(), 600)
        result = presence_service.get_statuses([self.user.pk, self.bob.pk])
        self.assertEqual(result[self.user.pk], 'online')
        self.assertEqual(result[self.bob.pk], 'offline')

    def test_respects_manual_overrides(self):
        presence_service.set_manual_status(self.user.pk, 'busy')
        result = presence_service.get_statuses([self.user.pk])
        self.assertEqual(result[self.user.pk], 'busy')

    def test_db_fallback_for_manual_status(self):
        UserPresence.objects.create(
            user=self.bob, last_seen=timezone.now(), manual_status='busy',
        )
        result = presence_service.get_statuses([self.bob.pk])
        self.assertEqual(result[self.bob.pk], 'busy')


# ── get_online_user_ids ─────────────────────────────────────────

class GetOnlineUserIdsTests(PresenceTestMixin, TestCase):

    def test_returns_recently_active_users(self):
        now = timezone.now()
        UserPresence.objects.create(user=self.user, last_seen=now, manual_status='auto')
        ids = presence_service.get_online_user_ids()
        self.assertIn(self.user.pk, ids)

    def test_excludes_invisible_users(self):
        now = timezone.now()
        UserPresence.objects.create(user=self.user, last_seen=now, manual_status='invisible')
        ids = presence_service.get_online_user_ids()
        self.assertNotIn(self.user.pk, ids)

    def test_includes_busy_users(self):
        now = timezone.now()
        UserPresence.objects.create(user=self.user, last_seen=now, manual_status='busy')
        ids = presence_service.get_online_user_ids()
        self.assertIn(self.user.pk, ids)

    def test_excludes_stale_auto_users(self):
        old = timezone.now() - timedelta(minutes=15)
        UserPresence.objects.create(user=self.user, last_seen=old, manual_status='auto')
        ids = presence_service.get_online_user_ids()
        self.assertNotIn(self.user.pk, ids)


# ── is_active ───────────────────────────────────────────────────

class IsActiveTests(PresenceTestMixin, TestCase):

    def test_false_when_no_data(self):
        self.assertFalse(presence_service.is_active(self.user.pk))

    def test_true_when_recently_active(self):
        now = timezone.now()
        cache.set(f'presence:activity:{self.user.pk}', now.isoformat(), 600)
        self.assertTrue(presence_service.is_active(self.user.pk))

    def test_false_when_stale(self):
        old = timezone.now() - timedelta(seconds=60)
        cache.set(f'presence:activity:{self.user.pk}', old.isoformat(), 600)
        self.assertFalse(presence_service.is_active(self.user.pk))


# ── clear ───────────────────────────────────────────────────────

class ClearTests(PresenceTestMixin, TestCase):

    def test_clear_removes_all_cache_keys(self):
        uid = self.user.pk
        cache.set(f'presence:{uid}', 'x', 600)
        cache.set(f'presence:activity:{uid}', 'x', 600)
        cache.set(f'presence:dbsync:{uid}', 'x', 600)
        cache.set(f'presence:manual:{uid}', 'x', 600)

        presence_service.clear(uid)

        self.assertIsNone(cache.get(f'presence:{uid}'))
        self.assertIsNone(cache.get(f'presence:activity:{uid}'))
        self.assertIsNone(cache.get(f'presence:dbsync:{uid}'))
        self.assertIsNone(cache.get(f'presence:manual:{uid}'))


# ── get_last_seen ───────────────────────────────────────────────

class GetLastSeenTests(PresenceTestMixin, TestCase):

    def test_none_when_no_data(self):
        self.assertIsNone(presence_service.get_last_seen(self.user.pk))

    def test_returns_from_cache(self):
        now = timezone.now()
        cache.set(f'presence:{self.user.pk}', now.isoformat(), 600)
        result = presence_service.get_last_seen(self.user.pk)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(
            result.timestamp(), now.timestamp(), delta=1,
        )

    def test_falls_back_to_db(self):
        now = timezone.now()
        UserPresence.objects.create(user=self.user, last_seen=now)
        result = presence_service.get_last_seen(self.user.pk)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(
            result.timestamp(), now.timestamp(), delta=1,
        )
