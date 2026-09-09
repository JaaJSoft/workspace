"""Tests for workspace.core.sse_registry.

Covers notify_sse's Redis-failure fallback path - in particular that the
user_id written to the warning log is scrubbed of CR/LF so a crafted
identifier cannot forge fake log lines (CWE-117, py/log-injection) - and the
per-user event mailbox: its cursor semantics, the read-time supersede
collapse, and the three ways it used to drop events (a second tab draining it
first, a reconnect with no way to resume, a lock that gave up).
"""

from types import SimpleNamespace
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

    def _read(self, slug, user_id, cursor=0):
        from workspace.core.sse_registry import read_user_events

        entries, cursor = read_user_events(slug, user_id, cursor)
        return [payload for _seq, payload in entries], cursor

    def test_push_then_read_round_trips_and_advances_the_cursor(self):
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "a", "n": 1})
        push_user_event("x", 7, {"type": "a", "n": 2})
        payloads, cursor = self._read("x", 7)
        self.assertEqual(payloads, [{"type": "a", "n": 1}, {"type": "a", "n": 2}])
        self.assertEqual(self._read("x", 7, cursor), ([], cursor))

    def test_mailboxes_are_per_slug_and_per_user(self):
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "a"})
        self.assertEqual(self._read("y", 7)[0], [])
        self.assertEqual(self._read("x", 8)[0], [])
        self.assertEqual(len(self._read("x", 7)[0]), 1)

    def test_supersedes_keeps_only_the_newest_payload_for_a_key(self):
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"job": "1", "p": 10}, supersedes=("job", "1"))
        push_user_event("x", 7, {"job": "2", "p": 0}, supersedes=("job", "2"))
        push_user_event("x", 7, {"job": "1", "p": 90}, supersedes=("job", "1"))
        self.assertEqual(
            self._read("x", 7)[0], [{"job": "2", "p": 0}, {"job": "1", "p": 90}]
        )

    def test_supersedes_applies_to_every_reader_independently(self):
        """The collapse is a read-time view, not a destructive edit of the log."""
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"job": "1", "p": 10}, supersedes=("job", "1"))
        push_user_event("x", 7, {"job": "1", "p": 90}, supersedes=("job", "1"))
        self.assertEqual(self._read("x", 7)[0], [{"job": "1", "p": 90}])
        self.assertEqual(self._read("x", 7)[0], [{"job": "1", "p": 90}])

    def test_push_wakes_the_stream(self):
        from workspace.core.sse_registry import push_user_event

        with patch("workspace.core.sse_registry.notify_sse") as notify:
            push_user_event("x", 7, {"type": "a"})
        notify.assert_called_once_with("x", 7)

    def test_two_readers_of_the_same_user_both_receive_every_event(self):
        """Regression: one tab's read used to empty the mailbox for the other."""
        from workspace.core.sse_registry import push_user_event

        tab_a = tab_b = 0
        push_user_event("x", 7, {"type": "a"})

        payloads_a, tab_a = self._read("x", 7, tab_a)
        payloads_b, tab_b = self._read("x", 7, tab_b)

        self.assertEqual(payloads_a, [{"type": "a"}])
        self.assertEqual(payloads_b, [{"type": "a"}])

        push_user_event("x", 7, {"type": "b"})
        self.assertEqual(self._read("x", 7, tab_a)[0], [{"type": "b"}])
        self.assertEqual(self._read("x", 7, tab_b)[0], [{"type": "b"}])

    def test_a_reader_resuming_from_a_cursor_gets_what_it_missed(self):
        """Regression: events queued while no stream listened were never sent."""
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "a"})
        _payloads, cursor = self._read("x", 7)

        # Stream is down here - forced reconnect, network drop, page navigation.
        push_user_event("x", 7, {"type": "b"})
        push_user_event("x", 7, {"type": "c"})

        self.assertEqual(self._read("x", 7, cursor)[0], [{"type": "b"}, {"type": "c"}])

    def test_a_push_landing_mid_read_is_delivered_by_the_next_read(self):
        """Regression: a drain used to clear whatever a concurrent push added."""
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "a"})
        real_get_many = cache.get_many

        def racing_get_many(keys, *args, **kwargs):
            found = real_get_many(keys, *args, **kwargs)
            push_user_event("x", 7, {"type": "b"})
            return found

        with patch.object(cache, "get_many", side_effect=racing_get_many):
            payloads, cursor = self._read("x", 7)

        self.assertEqual(payloads, [{"type": "a"}])
        self.assertEqual(self._read("x", 7, cursor)[0], [{"type": "b"}])

    def test_concurrent_pushes_all_get_a_sequence_number_of_their_own(self):
        """No read-modify-write of a shared list, so no lock left to lose."""
        import threading

        from workspace.core.sse_registry import push_user_event

        start = threading.Barrier(8)

        def push(worker):
            start.wait()
            for n in range(10):
                push_user_event("x", 7, {"type": "a", "who": worker, "n": n})

        threads = [threading.Thread(target=push, args=(w,)) for w in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        payloads, _cursor = self._read("x", 7)
        self.assertEqual(len(payloads), 80)
        self.assertEqual(len({(p["who"], p["n"]) for p in payloads}), 80)

    def test_a_cursor_from_an_expired_mailbox_rewinds_instead_of_swallowing(self):
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "a"})
        cache.clear()  # the whole mailbox, counter included, ages out
        push_user_event("x", 7, {"type": "b"})

        self.assertEqual(self._read("x", 7, 50)[0], [{"type": "b"}])

    def test_replay_is_bounded_by_the_window(self):
        from workspace.core import sse_registry as registry

        with patch.object(registry, "_MAILBOX_MAX_REPLAY", 3):
            for n in range(6):
                registry.push_user_event("x", 7, {"type": "a", "n": n})
            payloads, cursor = self._read("x", 7)

        self.assertEqual([p["n"] for p in payloads], [3, 4, 5])
        self.assertEqual(cursor, 6)


class MailboxProviderTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _provider(self, last_event_id=None):
        from workspace.core.sse_registry import MailboxSSEProvider

        class Provider(MailboxSSEProvider):
            slug = "x"

        return Provider(SimpleNamespace(id=7), last_event_id)

    def test_a_fresh_connection_starts_at_the_head(self):
        """The page just rendered from the database; don't replay its own past."""
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "old"})
        provider = self._provider()
        self.assertEqual(provider.get_initial_events(), [])

        push_user_event("x", 7, {"type": "new"})
        self.assertEqual(provider.poll(None), [("new", {"type": "new"}, "2")])

    def test_a_resumed_connection_replays_from_its_last_event_id(self):
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "a"})
        push_user_event("x", 7, {"type": "b"})

        provider = self._provider("1")
        self.assertEqual(provider.get_initial_events(), [("b", {"type": "b"}, "2")])

    def test_an_unusable_last_event_id_falls_back_to_the_head(self):
        from workspace.core.sse_registry import push_user_event

        push_user_event("x", 7, {"type": "a"})
        self.assertEqual(self._provider("not-a-number").get_initial_events(), [])

    def test_an_idle_poll_still_drains_the_mailbox(self):
        """A publish that raced the subscribe must not wait for the next one."""
        from workspace.core.sse_registry import push_user_event

        provider = self._provider()
        push_user_event("x", 7, {"type": "a"})
        self.assertEqual(provider.poll(None), [("a", {"type": "a"}, "1")])
