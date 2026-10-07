"""One Redis subscription per process, shared by every SSE stream it serves.

A stream stays open for up to ten minutes. A Pub/Sub connection of its own for
that long ties up one connection per open tab, and the Redis client pool caps a
process at a hundred: past that, every cache read of the worker fails as well.
The hub holds a single pattern subscription to every user's channel instead,
and wakes the streams of the user each notification is for.

Every process therefore receives every user's notifications, and drops those
nobody it serves is waiting for. That is cheaper than subscribing and
unsubscribing per stream on a connection another thread is reading from: a
notification is only the name of the provider that has something new.
"""

import itertools
import logging
import threading
import time
from collections import defaultdict

import orjson

from workspace.common.logging import scrub

from .sse_registry import sse_registry

logger = logging.getLogger(__name__)

# notify_sse() publishes on CHANNEL_PREFIX + user id.
CHANNEL_PREFIX = "sse:user:"

# Seconds before resubscribing after Redis dropped the connection.
_RECONNECT_DELAY = 5


class Subscription:
    """What one stream is waiting for: its user's provider notifications.

    Notifications for the same provider coalesce until the stream looks, which
    loses nothing: a provider reads its state from its own cursor, so one poll
    catches up on any number of notifications.
    """

    def __init__(self, hub, user_id):
        self.user_id = user_id
        self._hub = hub
        self._lock = threading.Lock()
        self._pending = set()
        self._wakeup = threading.Event()

    def notify(self, *slugs):
        with self._lock:
            self._pending.update(slugs)
            self._wakeup.set()

    def wait(self, timeout):
        """Slugs of the providers notified since the last call, waiting up to
        *timeout* seconds for the first one. Empty when none came."""
        self._wakeup.wait(timeout)
        with self._lock:
            self._wakeup.clear()
            notified, self._pending = self._pending, set()
        return notified

    def close(self):
        self._hub.unsubscribe(self)


class SSEHub:
    """Fans the notifications of one Pub/Sub connection out to the streams."""

    def __init__(self, redis_factory):
        self._redis_factory = redis_factory
        self._lock = threading.Lock()
        self._subscriptions = defaultdict(set)
        self._listener = None

    def subscribe(self, user_id):
        subscription = Subscription(self, user_id)
        with self._lock:
            self._subscriptions[user_id].add(subscription)
            # Started with the first stream rather than with the process, and
            # restarted should it ever have stopped.
            if self._listener is None or not self._listener.is_alive():
                self._listener = threading.Thread(
                    target=self._listen, name="sse-hub", daemon=True
                )
                self._listener.start()
        return subscription

    def unsubscribe(self, subscription):
        with self._lock:
            subscriptions = self._subscriptions.get(subscription.user_id)
            if subscriptions is None:
                return
            subscriptions.discard(subscription)
            if not subscriptions:
                del self._subscriptions[subscription.user_id]

    def _listen(self):
        """Dispatch notifications until the subscription ends cleanly.

        A lost connection is not an end: the subscription is set up again, and
        the streams catch up once Redis confirms it.
        """
        while True:
            try:
                pubsub = self._redis_factory().pubsub()
                try:
                    pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
                    for message in pubsub.listen():
                        if message["type"] == "psubscribe":
                            self._resync()
                        elif message["type"] == "pmessage":
                            self._dispatch(message)
                finally:
                    pubsub.close()
                return
            except Exception:
                logger.warning(
                    "SSE hub lost its Redis subscription, retrying in %ss",
                    _RECONNECT_DELAY,
                    exc_info=True,
                )
                time.sleep(_RECONNECT_DELAY)

    def _resync(self):
        """Wake every open stream for every provider.

        Run when Redis confirms the subscription: it drops whatever is published
        while nobody listens, which happens before the first confirmation and
        after a lost connection. Each provider then catches up from its own
        cursor, as on any notification.
        """
        slugs = tuple(sse_registry.get_all())
        with self._lock:
            subscriptions = tuple(
                itertools.chain.from_iterable(self._subscriptions.values())
            )
        for subscription in subscriptions:
            subscription.notify(*slugs)

    def _dispatch(self, message):
        channel = message.get("channel")
        try:
            if isinstance(channel, bytes):
                channel = channel.decode()
            user_id = int(channel.removeprefix(CHANNEL_PREFIX))
            slug = orjson.loads(message["data"])["provider"]
        except Exception:
            logger.warning("Dropped a malformed SSE notification on %s", scrub(channel))
            return
        with self._lock:
            subscriptions = tuple(self._subscriptions.get(user_id, ()))
        for subscription in subscriptions:
            subscription.notify(slug)


_hub = None
_hub_lock = threading.Lock()


def get_hub():
    """The process's hub, or None when the cache is not Redis."""
    global _hub
    if _hub is None:
        with _hub_lock:
            if _hub is None:
                try:
                    from django_redis import get_redis_connection

                    get_redis_connection("default")
                except Exception:
                    return None
                _hub = SSEHub(lambda: get_redis_connection("default"))
    return _hub
