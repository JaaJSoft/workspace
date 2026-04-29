"""Tests for workspace.core.views_sse global SSE stream generators.

Validates that both the Pub/Sub and polling generators honor the
_MAX_CONNECTION_SECONDS budget — they must return cleanly so the browser
can auto-reconnect, and the finally blocks must run (pubsub cleanup,
Prometheus gauge decrement).
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from workspace.core import views_sse

User = get_user_model()


class StreamMaxDurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='sse-user', password='p')

    def setUp(self):
        self.request = RequestFactory().get('/api/v1/stream')
        self.request.user = self.user

    def test_polling_stream_returns_when_max_duration_reached(self):
        with patch.object(views_sse, '_MAX_CONNECTION_SECONDS', -1), \
                patch.object(views_sse, '_init_providers', return_value={}), \
                patch('workspace.core.views_sse.time.sleep'):
            chunks = list(views_sse._event_stream_polling(self.request))

        # No providers -> no initial events; budget exhausted -> exit before any keepalive/poll.
        self.assertEqual(chunks, [])

    def test_pubsub_stream_returns_and_cleans_up_when_max_duration_reached(self):
        fake_pubsub = MagicMock()
        fake_pubsub.get_message.return_value = None
        fake_redis = MagicMock()
        fake_redis.pubsub.return_value = fake_pubsub

        with patch.object(views_sse, '_MAX_CONNECTION_SECONDS', -1), \
                patch.object(views_sse, '_init_providers', return_value={}):
            chunks = list(views_sse._event_stream_pubsub(self.request, fake_redis))

        self.assertEqual(chunks, [])
        # finally block must run: subscribe was set up, unsubscribe + close must mirror it.
        fake_pubsub.subscribe.assert_called_once_with(f'sse:user:{self.user.id}')
        fake_pubsub.unsubscribe.assert_called_once_with(f'sse:user:{self.user.id}')
        fake_pubsub.close.assert_called_once()


class StreamConstantTests(TestCase):
    def test_max_connection_seconds_is_aligned_with_nginx_proxy_timeout(self):
        # nginx ingress proxy-read-timeout is 600s; staying below it avoids the
        # proxy cutting our stream while the budget is still ticking server-side.
        self.assertLessEqual(views_sse._MAX_CONNECTION_SECONDS, 600)
        # Anything under a minute is sub-calibrated: forces too many reconnects
        # which re-emit all initial snapshots and re-init providers.
        self.assertGreaterEqual(views_sse._MAX_CONNECTION_SECONDS, 60)
