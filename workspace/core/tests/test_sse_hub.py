"""The per-process Redis subscription shared by every SSE stream."""

import queue
import threading
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase

from workspace.core import sse_hub
from workspace.core.sse_hub import SSEHub
from workspace.core.views import sse

User = get_user_model()


def _notification(user_id, slug):
    return {
        "type": "pmessage",
        "pattern": b"sse:user:*",
        "channel": f"sse:user:{user_id}".encode(),
        "data": f'{{"provider":"{slug}"}}'.encode(),
    }


class FakePubSub:
    """Hands out queued messages until it is given None.

    Also answers the per-channel calls of a stream subscribing on its own, so
    that a regression is caught by what it costs rather than by a missing fake.
    """

    def __init__(self, fail=False):
        self.fail = fail
        self.patterns = []
        self.messages = queue.Queue()
        self.closed = False

    def psubscribe(self, pattern):
        self.patterns.append(pattern)

    def subscribe(self, channel):
        pass

    def unsubscribe(self, channel):
        pass

    def get_message(self, timeout=None):
        return None

    def listen(self):
        if self.fail:
            raise ConnectionError("Redis went away")
        while (message := self.messages.get(timeout=10)) is not None:
            yield message

    def close(self):
        self.closed = True


class FakeRedis:
    def __init__(self, *pubsubs):
        self._pubsubs = list(pubsubs)
        self.opened = 0
        self.subscribed = threading.Event()

    def pubsub(self, **kwargs):
        self.opened += 1
        pubsub = self._pubsubs.pop(0) if len(self._pubsubs) > 1 else self._pubsubs[0]
        if not pubsub.fail:
            self.subscribed.set()
        return pubsub


def _stop(test, hub, *pubsubs):
    """End the listener thread once the test is over."""

    def stop():
        for pubsub in pubsubs:
            pubsub.messages.put(None)
        if hub._listener is not None:
            hub._listener.join(timeout=5)

    test.addCleanup(stop)


class SubscriptionTests(SimpleTestCase):
    def setUp(self):
        self.hub = SSEHub(redis_factory=None)
        # Dispatch is driven by hand: no listener thread, no Redis.
        patcher = patch.object(threading.Thread, "start")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_notification_wakes_every_stream_of_its_user_and_no_other(self):
        first_tab = self.hub.subscribe(1)
        second_tab = self.hub.subscribe(1)
        someone_else = self.hub.subscribe(2)

        self.hub._dispatch(_notification(1, "chat"))

        self.assertEqual(first_tab.wait(timeout=0), {"chat"})
        self.assertEqual(second_tab.wait(timeout=0), {"chat"})
        self.assertEqual(someone_else.wait(timeout=0), set())

    def test_notifications_for_one_provider_count_once(self):
        tab = self.hub.subscribe(1)

        for slug in ("chat", "chat", "files"):
            self.hub._dispatch(_notification(1, slug))

        self.assertEqual(tab.wait(timeout=0), {"chat", "files"})
        self.assertEqual(tab.wait(timeout=0), set())

    def test_a_closed_stream_is_no_longer_woken(self):
        closed = self.hub.subscribe(1)
        still_open = self.hub.subscribe(1)
        closed.close()

        self.hub._dispatch(_notification(1, "chat"))

        self.assertEqual(closed.wait(timeout=0), set())
        self.assertEqual(still_open.wait(timeout=0), {"chat"})

    def test_a_malformed_notification_is_dropped(self):
        tab = self.hub.subscribe(1)

        with self.assertLogs("workspace.core.sse_hub", "WARNING"):
            self.hub._dispatch({"channel": b"sse:user:1", "data": b"not json"})
            self.hub._dispatch(
                {"channel": b"sse:user:someone", "data": b'{"provider":"chat"}'}
            )

        self.assertEqual(tab.wait(timeout=0), set())

    def test_waiting_with_nothing_notified_times_out_empty(self):
        tab = self.hub.subscribe(1)

        started = time.monotonic()
        self.assertEqual(tab.wait(timeout=0.05), set())
        self.assertGreaterEqual(time.monotonic() - started, 0.04)


class ListenerTests(SimpleTestCase):
    def test_every_stream_of_the_process_shares_one_subscription(self):
        pubsub = FakePubSub()
        redis = FakeRedis(pubsub)
        hub = SSEHub(lambda: redis)
        _stop(self, hub, pubsub)

        tabs = [hub.subscribe(user_id) for user_id in (1, 1, 2)]
        self.assertTrue(redis.subscribed.wait(timeout=5))
        pubsub.messages.put(_notification(2, "chat"))

        self.assertEqual(tabs[2].wait(timeout=5), {"chat"})
        self.assertEqual(redis.opened, 1)
        self.assertEqual(pubsub.patterns, ["sse:user:*"])

    def test_the_subscription_is_set_up_again_after_losing_redis(self):
        lost, working = FakePubSub(fail=True), FakePubSub()
        redis = FakeRedis(lost, working)
        hub = SSEHub(lambda: redis)
        _stop(self, hub, working)

        with (
            patch.object(sse_hub, "_RECONNECT_DELAY", 0),
            self.assertLogs("workspace.core.sse_hub", "WARNING"),
        ):
            tab = hub.subscribe(1)
            self.assertTrue(redis.subscribed.wait(timeout=5))
        working.messages.put(_notification(1, "chat"))

        self.assertEqual(tab.wait(timeout=5), {"chat"})
        self.assertEqual(redis.opened, 2)
        self.assertTrue(lost.closed)


class StreamSharingTests(TestCase):
    """Through the stream itself: what each open tab costs in Redis."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sse-hub", password="p")

    def test_streams_of_one_process_open_one_redis_connection(self):
        pubsub = FakePubSub()
        redis = FakeRedis(pubsub)
        request = RequestFactory().get("/api/v1/stream")
        request.user = self.user

        with (
            patch("django_redis.get_redis_connection", return_value=redis),
            patch.object(sse_hub, "_hub", None),
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={}),
        ):
            for _ in range(3):
                list(sse._event_stream(request))
            hub = sse_hub.get_hub()
            _stop(self, hub, pubsub)
            self.assertTrue(redis.subscribed.wait(timeout=5))

        self.assertEqual(redis.opened, 1)
