import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

import orjson
from django.core.cache import cache
from django.utils import timezone

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSEProviderInfo:
    slug: str
    provider_cls: type  # subclass of SSEProvider


class SSEProvider(ABC):
    """Instantiated once per SSE connection per provider."""

    def __init__(self, user, last_event_id: str | None):
        self.user = user
        self.last_event_id = last_event_id

    @abstractmethod
    def get_initial_events(self) -> list[tuple[str, dict, str | None]]:
        """Events sent immediately on connection.

        Returns list of (event_name, data_dict, event_id_or_None).
        """

    @abstractmethod
    def poll(self, cache_value: str | None) -> list[tuple[str, dict, str | None]]:
        """Called every ~2s.

        cache_value is non-None when the dirty flag changed, None otherwise.
        Returns list of (event_name, data_dict, event_id_or_None).
        """


class SSERegistry:
    """Singleton thread-safe registry for SSE providers."""

    def __init__(self):
        self._providers: dict[str, SSEProviderInfo] = {}
        self._lock = threading.Lock()

    def register(self, provider_info: SSEProviderInfo):
        with self._lock:
            if provider_info.slug in self._providers:
                raise ValueError(
                    f"SSE provider with slug '{provider_info.slug}' is already registered"
                )
            self._providers[provider_info.slug] = provider_info

    def get_all(self) -> dict[str, SSEProviderInfo]:
        return dict(self._providers)


sse_registry = SSERegistry()


def _get_redis():
    """Return a raw Redis connection, or None if Redis is not the cache backend."""
    try:
        from django_redis import get_redis_connection

        return get_redis_connection("default")
    except Exception:
        return None


def notify_sse(provider_slug: str, user_id: int):
    """Notify an SSE provider that new data is available for a user.

    Uses Redis Pub/Sub for near-instant delivery when Redis is available,
    falls back to cache dirty flags for local dev without Redis.
    """
    redis = _get_redis()
    if redis is not None:
        try:
            redis.publish(
                f"sse:user:{user_id}",
                orjson.dumps({"provider": provider_slug}),
            )
            return
        except Exception:
            logger.warning(
                "Redis publish failed for SSE notify (provider=%s, user=%s), "
                "falling back to cache",
                provider_slug,
                scrub(user_id),
                exc_info=True,
            )

    # Fallback: cache dirty flag (local dev or Redis failure)
    cache.set(
        f"sse:{provider_slug}:last_event:{user_id}",
        timezone.now().isoformat(),
        120,
    )


# -- per-user event mailbox ---------------------------------------------------
#
# Providers that fan out discrete events (a file changed, an import progressed)
# append them to a per-user log and wake the stream; every stream of that user
# reads the log from its own cursor. Reads are non-destructive, so two tabs both
# see the event instead of racing for it, and a stream that reconnects resumes
# from the sequence number it last received rather than from whatever is left.
#
# Each entry lives under its own cache key, numbered by an atomic counter: a
# push writes one key nobody else writes, a read only reads. There is no
# read-modify-write of a shared list, and therefore no mutex whose failure
# would silently drop an event.

_MAILBOX_ENTRY_KEY = "sse:{slug}:mailbox:{user_id}:{seq}"
_MAILBOX_SEQ_KEY = "sse:{slug}:mailbox:{user_id}:seq"
_MAILBOX_TTL = 300
# The counter must outlive the newest entry it numbered: were it to expire
# first, sequence numbers would restart below the cursor of a live stream.
_MAILBOX_SEQ_TTL = _MAILBOX_TTL * 12
# Ceiling on how far back a resuming stream replays, so one read stays bounded.
_MAILBOX_MAX_REPLAY = 500


def _entry_key(slug, user_id, seq):
    return _MAILBOX_ENTRY_KEY.format(slug=slug, user_id=user_id, seq=seq)


def _next_seq(slug, user_id):
    """Hand out the next sequence number for a mailbox, atomically."""
    key = _MAILBOX_SEQ_KEY.format(slug=slug, user_id=user_id)
    cache.add(key, 0, _MAILBOX_SEQ_TTL)
    try:
        seq = cache.incr(key)
    except ValueError:
        # The counter expired between the add and the incr.
        cache.add(key, 0, _MAILBOX_SEQ_TTL)
        seq = cache.incr(key)
    cache.touch(key, _MAILBOX_SEQ_TTL)
    return seq


def mailbox_head(slug, user_id):
    """Highest sequence number handed out for this mailbox (0 when empty)."""
    return cache.get(_MAILBOX_SEQ_KEY.format(slug=slug, user_id=user_id)) or 0


def push_user_event(slug, user_id, payload, *, supersedes=None):
    """Queue *payload* for *user_id* on provider *slug* and wake the stream.

    ``supersedes=(field, value)`` drops earlier queued payloads carrying the
    same value - for progress-style events where only the newest matters. The
    collapse happens when a stream reads, so it costs the push nothing and
    applies to every reader independently.
    """
    seq = _next_seq(slug, user_id)
    entry = {"seq": seq, "payload": payload}
    if supersedes is not None:
        field, value = supersedes
        entry["supersedes"] = [field, value]
    cache.set(_entry_key(slug, user_id, seq), entry, _MAILBOX_TTL)
    notify_sse(slug, user_id)


def _collapse_superseded(entries):
    """Drop entries a later one supersedes, keeping the survivors in order."""
    kept = []
    for entry in entries:
        supersedes = entry.get("supersedes")
        if supersedes:
            field, value = supersedes
            kept = [e for e in kept if e["payload"].get(field) != value]
        kept.append(entry)
    return [(entry["seq"], entry["payload"]) for entry in kept]


def read_user_events(slug, user_id, cursor):
    """Return ``(entries, new_cursor)`` for what was queued after *cursor*.

    ``entries`` is a list of ``(seq, payload)`` in queue order. The read leaves
    the mailbox untouched: the caller advances its own cursor and every other
    stream of the same user still sees the same entries.
    """
    head = mailbox_head(slug, user_id)
    if head < cursor:
        # The counter expired and restarted; a cursor from the previous
        # incarnation would swallow every event until it caught up again.
        cursor = 0
    if head == cursor:
        return [], cursor
    low = max(cursor, head - _MAILBOX_MAX_REPLAY)
    keys = [_entry_key(slug, user_id, seq) for seq in range(low + 1, head + 1)]
    found = cache.get_many(keys)
    entries = sorted(
        (e for e in found.values() if isinstance(e, dict) and "payload" in e),
        key=lambda e: e["seq"],
    )
    # A push takes the sequence number first and writes the entry second, so
    # the newest numbers can be reserved while their entries are still in
    # flight. Stopping below the highest one that is missing leaves them for
    # the next read instead of stepping over them; `low` floors the walk so an
    # entry that genuinely expired cannot stall the cursor for good.
    found_seqs = {entry["seq"] for entry in entries}
    new_cursor = head
    while new_cursor > low and new_cursor not in found_seqs:
        new_cursor -= 1
    return _collapse_superseded(entries), new_cursor


class MailboxSSEProvider(SSEProvider):
    """Provider whose events all come from the per-user mailbox.

    Subclasses only set ``slug``. The cursor lives on the connection: it
    resumes from the id the browser replays on a reconnect, and otherwise
    starts at the head, because a page that has just rendered from the database
    already reflects everything queued before it opened the stream.
    """

    slug: str

    def __init__(self, user, last_event_id):
        super().__init__(user, last_event_id)
        self._cursor = self._seed_cursor(last_event_id)

    def _seed_cursor(self, last_event_id):
        try:
            return max(int(last_event_id), 0)
        except TypeError, ValueError:
            return mailbox_head(self.slug, self.user.id)

    def get_initial_events(self):
        return self._read()

    def poll(self, cache_value):
        return self._read()

    def _read(self):
        entries, self._cursor = read_user_events(self.slug, self.user.id, self._cursor)
        return [(payload["type"], payload, str(seq)) for seq, payload in entries]
