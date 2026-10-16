from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

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
