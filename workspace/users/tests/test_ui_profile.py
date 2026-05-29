from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class ProfileAsyncFeedTests(TestCase):
    """The profile page defers its activity feed to an async load, mirroring
    the dashboard, so the initial render skips the per-provider feed fan-out."""

    def setUp(self):
        self.user = User.objects.create_user(username='profuser', password='pass123')
        self.client.login(username='profuser', password='pass123')

    def tearDown(self):
        cache.clear()

    @patch('workspace.users.ui.views.get_recent_events')
    def test_does_not_compute_feed_on_initial_render(self, mock_events):
        mock_events.return_value = []
        resp = self.client.get(reverse('users_ui:profile'))
        self.assertEqual(resp.status_code, 200)
        mock_events.assert_not_called()

    def test_renders_async_feed_loader(self):
        resp = self.client.get(reverse('users_ui:profile'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "$ajax(feedUrl, { target: 'profile-activity' })")
