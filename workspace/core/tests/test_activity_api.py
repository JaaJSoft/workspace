from contextlib import ExitStack
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.core.module_registry import registry
from workspace.photos.activity import PhotosActivityProvider

User = get_user_model()


class ActivityRecentViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@test.com",
            password="pass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_recent_requires_auth(self):
        client = APIClient()
        resp = client.get("/api/v1/activity/recents")
        self.assertIn(resp.status_code, (401, 403))

    def test_recent_returns_200(self):
        resp = self.client.get("/api/v1/activity/recents")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("events", resp.json())
        self.assertIn("sources", resp.json())


class ActivityDailyCountsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@test.com",
            password="pass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_daily_counts_requires_date_params(self):
        resp = self.client.get("/api/v1/activity/daily-counts")
        self.assertEqual(resp.status_code, 400)

    def test_daily_counts_returns_200(self):
        resp = self.client.get(
            "/api/v1/activity/daily-counts",
            {
                "date_from": "2026-03-01",
                "date_to": "2026-03-31",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("counts", resp.json())


class ActivityStatsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@test.com",
            password="pass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_stats_requires_auth(self):
        client = APIClient()
        resp = client.get("/api/v1/activity/stats")
        self.assertIn(resp.status_code, (401, 403))

    def test_stats_returns_200(self):
        resp = self.client.get("/api/v1/activity/stats")
        self.assertEqual(resp.status_code, 200)


@override_settings(PREVIEW_VISIBILITY="staff")
class ActivityApiPreviewModuleVisibilityTests(TestCase):
    """A preview module hidden from the caller contributes nothing to the
    activity endpoints, and naming it as a source answers like an unknown one."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="vis-api", password="x")
        self.staff = User.objects.create_user(
            username="vis-api-staff", password="x", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        event = {"label": "photo-event", "timestamp": timezone.now()}
        stack = ExitStack()
        self.addCleanup(stack.close)
        # No module in preview has an activity provider: put Photos back there.
        stack.enter_context(
            patch.dict(
                registry._modules,
                {"photos": replace(registry.get("photos"), preview=True)},
            )
        )
        stack.enter_context(
            patch.object(
                PhotosActivityProvider,
                "get_recent_events",
                side_effect=lambda *a, **k: [dict(event)],
            )
        )
        stack.enter_context(
            patch.object(
                PhotosActivityProvider,
                "get_daily_counts",
                return_value={date(2026, 3, 2): 41},
            )
        )
        stack.enter_context(
            patch.object(
                PhotosActivityProvider, "get_stats", return_value={"albums": 3}
            )
        )

    def tearDown(self):
        cache.clear()

    def test_hidden_source_answers_like_an_unknown_source(self):
        hidden = self.client.get("/api/v1/activity/recents", {"source": "photos"})
        unknown = self.client.get("/api/v1/activity/recents", {"source": "nope"})

        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.json(), unknown.json())
        self.assertEqual(hidden.json()["events"], [])

    def test_all_feed_and_sources_leave_the_hidden_module_out(self):
        body = self.client.get("/api/v1/activity/recents").json()

        self.assertNotIn("photos", [s["slug"] for s in body["sources"]])
        self.assertNotIn("photos", [e["source"] for e in body["events"]])

    def test_daily_counts_leave_the_hidden_module_out(self):
        resp = self.client.get(
            "/api/v1/activity/daily-counts",
            {"date_from": "2026-03-01", "date_to": "2026-03-31"},
        )

        self.assertEqual(resp.json()["counts"].get("2026-03-02", 0), 0)

    def test_stats_leave_the_hidden_module_out(self):
        resp = self.client.get("/api/v1/activity/stats")

        self.assertNotIn("photos", resp.json())

    def test_staff_still_sees_the_preview_module(self):
        self.client.force_authenticate(user=self.staff)

        recents = self.client.get(
            "/api/v1/activity/recents", {"source": "photos"}
        ).json()
        counts = self.client.get(
            "/api/v1/activity/daily-counts",
            {"date_from": "2026-03-01", "date_to": "2026-03-31"},
        ).json()["counts"]
        stats = self.client.get("/api/v1/activity/stats").json()

        self.assertEqual([e["label"] for e in recents["events"]], ["photo-event"])
        self.assertIn("photos", [s["slug"] for s in recents["sources"]])
        self.assertEqual(counts["2026-03-02"], 41)
        self.assertEqual(stats["photos"], {"albums": 3})


class ActivityTargetUserTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="targetuser", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        cache.clear()

    def test_a_user_id_that_is_not_a_number_is_a_400(self):
        for path in (
            "recents?user_id=abc",
            "daily-counts?date_from=2026-01-01&date_to=2026-01-31&user_id=abc",
            "stats?user_id=abc",
        ):
            with self.subTest(path=path):
                resp = self.client.get(f"/api/v1/activity/{path}")
                self.assertEqual(resp.status_code, 400)


class ActivityOffsetCapTests(TestCase):
    """An offset past the feed's depth reads no provider: each would load
    offset + limit rows to merge them."""

    def setUp(self):
        self.user = User.objects.create_user(username="pager", password="pass123")

    def _fetch(self, offset):
        from django.urls import reverse

        api = APIClient()
        api.force_authenticate(user=self.user)
        self.client.force_login(self.user)
        target = "workspace.core.activity_registry.ActivityRegistry.get_recent_events"
        with patch(target, return_value=[]) as providers:
            api_resp = api.get(f"/api/v1/activity/recents?offset={offset}")
            feed_resp = self.client.get(
                f"{reverse('dashboard:activity_feed')}?offset={offset}",
                HTTP_X_ALPINE_REQUEST="true",
            )
        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual(feed_resp.status_code, 200)
        return providers

    def test_a_huge_offset_reads_nothing(self):
        providers = self._fetch(100_000_000)

        providers.assert_not_called()

    def test_an_offset_within_the_feed_still_reads(self):
        from workspace.core.services.activity import MAX_ACTIVITY_OFFSET

        providers = self._fetch(MAX_ACTIVITY_OFFSET)

        self.assertEqual(providers.call_count, 2)
