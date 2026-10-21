"""What the presence stream sends, and to whom.

A stream carries the status of the users its viewer *watches*: the ids the
viewer's pages asked about (``POST /api/v1/users/presence``), plus the viewer
themself. It sends a snapshot on connect, then only the statuses that changed.

Statuses come from one table read per process every few seconds
(:func:`instance_statuses`), shared by every stream and by the API, so the two
never disagree. Each stream then only looks up its own watched ids in it, which
keeps its cost proportional to what it shows rather than to the instance.

The watch list is kept per user, not per tab: every tab of a user follows the
union of what any of them displays. A tab therefore receives a few statuses it
does not render, which it stores and ignores; in exchange there is no tab
identity to carry between the page and the stream.
"""

import threading
import time

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from . import presence as presence_service

# How long one read of the presence table serves every stream of the process.
INSTANCE_STATUSES_TTL = 5  # seconds
# Oldest ids drop out first: a long-lived session keeps following what it
# displayed recently, not everything it ever came across.
WATCH_LIMIT = 1000
# Refreshed on every write; the page re-sends its ids on each stream reconnect
# (at most every 10 minutes), so an open tab never lets it lapse.
WATCH_TTL = 3600  # seconds
# Upper bound on one request, so a single call stays a bounded lookup.
MAX_IDS_PER_REQUEST = 500

_BOT_IDS_KEY = "presence:bot_user_ids"
_BOT_IDS_TTL = 300  # seconds

_instance_lock = threading.Lock()
# Mutated in place so no `global` rebinding is needed.
_instance_cache = {"value": None, "ts": 0}


def _watch_key(user_id: int) -> str:
    return f"presence:watch:{user_id}"


def bot_user_ids() -> list[int]:
    """Bot user ids. Bots have no presence row: their status is their identity."""
    bot_ids = cache.get(_BOT_IDS_KEY)
    if bot_ids is None:
        from workspace.ai.models import BotProfile

        bot_ids = list(BotProfile.objects.values_list("user_id", flat=True))
        cache.set(_BOT_IDS_KEY, bot_ids, _BOT_IDS_TTL)
    return bot_ids


def _query_instance_statuses() -> dict[int, str]:
    from workspace.users.models import UserPresence

    now = timezone.now()
    cutoff = now - presence_service.AWAY_THRESHOLD
    online_cutoff = now - presence_service.ONLINE_THRESHOLD

    rows = UserPresence.objects.filter(
        Q(last_seen__gte=cutoff) & ~Q(manual_status="invisible")
        | Q(manual_status__in=("busy", "away"))
    ).values_list("user_id", "last_seen", "manual_status")

    statuses = {}
    for uid, last_seen, manual in rows:
        match manual:
            case "busy" | "away":
                statuses[uid] = manual
            case "auto" | "online":
                if last_seen >= online_cutoff:
                    statuses[uid] = "online"
                elif last_seen >= cutoff:
                    statuses[uid] = "away"
    for uid in bot_user_ids():
        statuses[uid] = "bot"
    return statuses


def instance_statuses() -> dict[int, str]:
    """Status of every user who is not offline, cached per process.

    Absent means offline. Callers must not mutate the returned dict: it is
    shared by every stream of the process until the next refresh.
    """
    if (
        _instance_cache["value"] is not None
        and time.monotonic() - _instance_cache["ts"] < INSTANCE_STATUSES_TTL
    ):
        return _instance_cache["value"]

    with _instance_lock:
        if (
            _instance_cache["value"] is not None
            and time.monotonic() - _instance_cache["ts"] < INSTANCE_STATUSES_TTL
        ):
            return _instance_cache["value"]
        _instance_cache["value"] = _query_instance_statuses()
        _instance_cache["ts"] = time.monotonic()
        return _instance_cache["value"]


def statuses_of(user_ids) -> dict[int, str]:
    """Status of each of *user_ids*, ``"offline"`` included."""
    statuses = instance_statuses()
    return {uid: statuses.get(uid, "offline") for uid in user_ids}


def watch(user_id: int, user_ids: list[int]) -> bool:
    """Make *user_id*'s streams follow *user_ids*. True when the list grew.

    Read-modify-write without a lock: two tabs writing at the same instant can
    lose one side's ids, which the losing tab sends again on its next reconnect.
    """
    key = _watch_key(user_id)
    current = cache.get(key) or []
    added = dict.fromkeys(user_ids)
    merged = [uid for uid in current if uid not in added] + list(added)
    cache.set(key, merged[-WATCH_LIMIT:], WATCH_TTL)
    return not added.keys() <= set(current)


def watched_user_ids(user_id: int) -> set[int]:
    """Ids *user_id*'s streams follow, the viewer included."""
    return {*(cache.get(_watch_key(user_id)) or ()), user_id}


def as_payload(statuses: dict[int, str]) -> dict[str, str]:
    """Wire form of a status map: JSON object keys are strings."""
    return {str(uid): status for uid, status in statuses.items()}


def as_snapshot(statuses: dict[int, str]) -> dict[str, list[int]]:
    """Bucketed form of a status map, the shape of ``presence_snapshot``."""
    buckets = {"online": [], "away": [], "busy": [], "bot": []}
    for uid, status in statuses.items():
        if status in buckets:
            buckets[status].append(uid)
    return buckets
