"""Tests for workspace.core.sse_registry.notify_sse.

Covers the Redis-failure fallback path and, in particular, that the
user_id written to the warning log is scrubbed of CR/LF so a crafted
identifier cannot forge fake log lines (CWE-117, py/log-injection).
"""

from unittest.mock import MagicMock, patch

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
