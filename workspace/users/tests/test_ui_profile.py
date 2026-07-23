from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


class ProfileAsyncFeedTests(TestCase):
    """The profile page defers its activity feed to an async load, mirroring
    the dashboard, so the initial render skips the per-provider feed fan-out."""

    def setUp(self):
        self.user = User.objects.create_user(username="profuser", password="pass123")
        self.client.login(username="profuser", password="pass123")

    def tearDown(self):
        cache.clear()

    @patch("workspace.users.ui.views.get_recent_events")
    def test_does_not_compute_feed_on_initial_render(self, mock_events):
        mock_events.return_value = []
        resp = self.client.get(reverse("users_ui:profile"))
        self.assertEqual(resp.status_code, 200)
        mock_events.assert_not_called()

    def test_renders_async_feed_loader(self):
        resp = self.client.get(reverse("users_ui:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "$ajax(feedUrl, { target: 'profile-activity' })")


class ProfileActivityVisibilityTests(TestCase):
    """The profile activity tabs must follow the *viewer*'s module visibility:
    a preview module (projects, staff-only by default) must not appear for a
    non-staff viewer, on any profile. Uses the real registries."""

    def setUp(self):
        self.normal = User.objects.create_user(username="visnorm", password="pass123")
        self.staff = User.objects.create_user(
            username="visstaff", password="pass123", is_staff=True
        )

    def tearDown(self):
        cache.clear()

    def _source_slugs(self, resp):
        return [s["slug"] for s in resp.context["activity_sources"]]

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_preview_tab_hidden_from_normal_viewer_on_own_profile(self):
        self.client.login(username="visnorm", password="pass123")
        resp = self.client.get(reverse("users_ui:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("projects", self._source_slugs(resp))

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_preview_tab_visible_to_staff_viewer(self):
        self.client.login(username="visstaff", password="pass123")
        resp = self.client.get(reverse("users_ui:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("projects", self._source_slugs(resp))

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_visibility_follows_viewer_not_profile_owner(self):
        # A normal user viewing a staff member's profile must still not see
        # the preview tab: the filter keys off who is looking, not whose
        # profile it is.
        self.client.login(username="visnorm", password="pass123")
        resp = self.client.get(
            reverse(
                "users_ui:profile_by_username",
                kwargs={"username": "visstaff"},
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("projects", self._source_slugs(resp))

    @override_settings(PREVIEW_VISIBILITY="staff")
    @patch("workspace.users.ui.views.get_sources")
    @patch("workspace.users.ui.views.get_recent_events")
    def test_feed_partial_forwards_viewer_to_service(self, mock_events, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = []
        self.client.login(username="visnorm", password="pass123")

        resp = self.client.get(
            reverse(
                "users_ui:profile_activity_feed",
                kwargs={"username": "visstaff"},
            ),
            HTTP_X_ALPINE_REQUEST="true",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_events.call_args.kwargs["visible_to"], self.normal)
        mock_sources.assert_called_once_with(self.normal)


class ProfileHeatmapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="heatuser", password="pass123")
        self.client.login(username="heatuser", password="pass123")

    def tearDown(self):
        cache.clear()

    def test_profile_page_renders_heatmap(self):
        resp = self.client.get(reverse("users_ui:profile"))
        self.assertEqual(resp.status_code, 200)
        heatmap = resp.context["heatmap"]
        self.assertEqual(len(heatmap["weeks"]), 52)
        self.assertEqual(len(heatmap["month_labels"]), 52)
        self.assertEqual(heatmap["total_contributions"], 0)

    def test_daily_counts_fan_out_runs_once_across_repeat_views(self):
        """The per-provider 12-month aggregate fan-out is cached: a second
        profile render within the TTL must not hit the providers again."""
        with patch(
            "workspace.core.services.activity.activity_registry"
        ) as mock_registry:
            mock_registry.get_daily_counts.return_value = {}
            mock_registry.get_stats.return_value = {}
            self.client.get(reverse("users_ui:profile"))
            self.client.get(reverse("users_ui:profile"))
            self.assertEqual(mock_registry.get_daily_counts.call_count, 1)
