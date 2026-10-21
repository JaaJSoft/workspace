"""Tests for workspace.users.services.presence_stream.

Exercises the DB-to-status mapping, the process cache shared by every stream,
and the per-user watch list. The process cache and the Django cache are reset
between tests to avoid cross-test bleed.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.users.models import UserPresence
from workspace.users.services import presence_stream

User = get_user_model()


class PresenceStreamResetMixin:
    def setUp(self):
        super().setUp()
        presence_stream._instance_cache["value"] = None
        presence_stream._instance_cache["ts"] = 0
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()


class InstanceStatusesTests(PresenceStreamResetMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()

        def _presence(username, *, delta, manual="auto"):
            user = User.objects.create_user(username=username, password="pass")
            UserPresence.objects.create(
                user=user,
                last_seen=now - delta,
                last_activity=now - delta,
                manual_status=manual,
            )
            return user

        cls.online_user = _presence("online", delta=timedelta(seconds=30))
        cls.away_by_age = _presence("away_age", delta=timedelta(minutes=5))
        cls.offline_user = _presence("offline", delta=timedelta(minutes=30))
        cls.busy_user = _presence("busy", delta=timedelta(minutes=30), manual="busy")
        cls.manual_away = _presence(
            "manual_away", delta=timedelta(minutes=30), manual="away"
        )
        cls.invisible_user = _presence(
            "invisible", delta=timedelta(seconds=10), manual="invisible"
        )

    def test_maps_users_to_their_status(self):
        with mock.patch.object(presence_stream, "bot_user_ids", return_value=[]):
            statuses = presence_stream._query_instance_statuses()

        self.assertEqual(statuses[self.online_user.id], "online")
        self.assertEqual(statuses[self.away_by_age.id], "away")
        self.assertEqual(statuses[self.manual_away.id], "away")
        self.assertEqual(statuses[self.busy_user.id], "busy")
        # Invisible users never surface even though they were recently active.
        self.assertNotIn(self.invisible_user.id, statuses)
        # Offline (auto, stale) users are omitted.
        self.assertNotIn(self.offline_user.id, statuses)

    def test_bots_are_marked_as_bots(self):
        with mock.patch.object(presence_stream, "bot_user_ids", return_value=[42]):
            statuses = presence_stream._query_instance_statuses()
        self.assertEqual(statuses[42], "bot")

    def test_statuses_of_reports_offline_explicitly(self):
        with mock.patch.object(presence_stream, "bot_user_ids", return_value=[]):
            statuses = presence_stream.statuses_of(
                [self.online_user.id, self.offline_user.id]
            )
        self.assertEqual(
            statuses,
            {self.online_user.id: "online", self.offline_user.id: "offline"},
        )


class InstanceStatusesCacheTests(PresenceStreamResetMixin, TestCase):
    def test_cache_hit_reuses_previous_read(self):
        with mock.patch.object(
            presence_stream, "_query_instance_statuses", return_value={1: "online"}
        ) as query:
            first = presence_stream.instance_statuses()
            second = presence_stream.instance_statuses()

        query.assert_called_once()
        self.assertIs(first, second)

    def test_expired_cache_reads_again(self):
        with mock.patch.object(
            presence_stream, "_query_instance_statuses", return_value={1: "online"}
        ):
            presence_stream.instance_statuses()
        presence_stream._instance_cache["ts"] = 0

        with mock.patch.object(
            presence_stream,
            "_query_instance_statuses",
            return_value={1: "online", 2: "away"},
        ) as query:
            statuses = presence_stream.instance_statuses()

        query.assert_called_once()
        self.assertEqual(statuses, {1: "online", 2: "away"})


class BotIdsCacheTests(PresenceStreamResetMixin, TestCase):
    def test_bot_ids_are_cached(self):
        # ai/0002_create_default_bot may have seeded a row when AI_API_KEY is
        # set (e.g. in local dev via .env), so don't assume the table is empty.
        from workspace.ai.models import BotProfile

        bot_user = User.objects.create_user(username="bot1", password="pass")
        BotProfile.objects.create(user=bot_user)

        ids = presence_stream.bot_user_ids()
        self.assertIn(bot_user.id, ids)

        # Stale reads for up to the TTL are the property under test.
        BotProfile.objects.filter(user=bot_user).delete()
        self.assertEqual(presence_stream.bot_user_ids(), ids)


class WatchTests(PresenceStreamResetMixin, TestCase):
    def test_the_viewer_always_watches_themself(self):
        self.assertEqual(presence_stream.watched_user_ids(7), {7})

    def test_watch_adds_ids_for_every_stream_of_the_user(self):
        presence_stream.watch(7, [1, 2])
        presence_stream.watch(7, [3])
        self.assertEqual(presence_stream.watched_user_ids(7), {1, 2, 3, 7})
        # Another viewer's list is untouched.
        self.assertEqual(presence_stream.watched_user_ids(8), {8})

    def test_watch_reports_whether_the_list_grew(self):
        self.assertTrue(presence_stream.watch(7, [1, 2]))
        self.assertFalse(presence_stream.watch(7, [2, 1]))
        self.assertTrue(presence_stream.watch(7, [2, 3]))

    def test_the_least_recently_watched_ids_drop_out_past_the_limit(self):
        with mock.patch.object(presence_stream, "WATCH_LIMIT", 3):
            presence_stream.watch(7, [1, 2, 3])
            # Watching 1 again makes it the most recent, so 2 goes first.
            presence_stream.watch(7, [1, 4])
        self.assertEqual(presence_stream.watched_user_ids(7), {3, 1, 4, 7})


class PayloadTests(TestCase):
    def test_snapshot_buckets_statuses_and_drops_offline(self):
        snapshot = presence_stream.as_snapshot(
            {1: "online", 2: "away", 3: "busy", 4: "bot", 5: "offline"}
        )
        self.assertEqual(
            snapshot, {"online": [1], "away": [2], "busy": [3], "bot": [4]}
        )

    def test_payload_keys_are_strings(self):
        self.assertEqual(
            presence_stream.as_payload({1: "online", 5: "offline"}),
            {"1": "online", "5": "offline"},
        )
