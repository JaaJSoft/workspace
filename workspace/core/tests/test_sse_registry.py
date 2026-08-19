"""Tests for workspace.core.sse_registry.notify_sse.

Covers the Redis-failure fallback path and, in particular, that the
user_id written to the warning log is scrubbed of CR/LF so a crafted
identifier cannot forge fake log lines (CWE-117, py/log-injection).
"""

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from workspace.core import sse_registry


class NotifySseLogInjectionTests(TestCase):
    def test_redis_failure_log_scrubs_user_id(self):
        """A user_id carrying CR/LF must not break the warning into extra lines."""
        redis = MagicMock()
        redis.publish.side_effect = RuntimeError("boom")

        with patch.object(sse_registry, "_get_redis", return_value=redis):
            with self.assertLogs("workspace.core.sse_registry", level="WARNING") as cm:
                sse_registry.notify_sse("chat", "42\r\nForged log line")

        # One record only, and its rendered message stays on conceptual single
        # fields: no raw CR/LF from the user-controlled id leaks into the output.
        self.assertEqual(len(cm.records), 1)
        message = cm.records[0].getMessage()
        self.assertNotIn("\r", message)
        self.assertNotIn("\n", message)
        self.assertIn("42Forged log line", message)


class UserEventMailboxTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_push_then_drain_round_trips_and_clears(self):
        from workspace.core.sse_registry import drain_user_events, push_user_event

        push_user_event("x", 7, {"type": "a", "n": 1})
        push_user_event("x", 7, {"type": "a", "n": 2})
        self.assertEqual(
            drain_user_events("x", 7), [{"type": "a", "n": 1}, {"type": "a", "n": 2}]
        )
        self.assertEqual(drain_user_events("x", 7), [])

    def test_mailboxes_are_per_slug_and_per_user(self):
        from workspace.core.sse_registry import drain_user_events, push_user_event

        push_user_event("x", 7, {"type": "a"})
        self.assertEqual(drain_user_events("y", 7), [])
        self.assertEqual(drain_user_events("x", 8), [])
        self.assertEqual(len(drain_user_events("x", 7)), 1)

    def test_supersedes_keeps_only_the_newest_payload_for_a_key(self):
        from workspace.core.sse_registry import drain_user_events, push_user_event

        push_user_event("x", 7, {"job": "1", "p": 10}, supersedes=("job", "1"))
        push_user_event("x", 7, {"job": "2", "p": 0}, supersedes=("job", "2"))
        push_user_event("x", 7, {"job": "1", "p": 90}, supersedes=("job", "1"))
        self.assertEqual(
            drain_user_events("x", 7), [{"job": "2", "p": 0}, {"job": "1", "p": 90}]
        )

    def test_push_wakes_the_stream(self):
        from workspace.core.sse_registry import push_user_event

        with patch("workspace.core.sse_registry.notify_sse") as notify:
            push_user_event("x", 7, {"type": "a"})
        notify.assert_called_once_with("x", 7)
