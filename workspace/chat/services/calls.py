"""Call lifecycle, presence and cleanup for chat voice rooms.

Durable state (CallSession, CallParticipant, the system message) is in the DB.
Live presence is a cache heartbeat (auto-expiring) so a crashed/closed tab is
reaped without a clean "leave". Lifecycle mutations fan out cache events via
``call_signaling`` so the SSE poll delivers them in near real time.
"""

from django.conf import settings
from django.core.cache import cache

DEFAULT_MEDIA_STATE = {"audio": True}


def format_duration(seconds):
    """Human label for a call duration. No em-dash; uses 'min' / 'h'."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} h {minutes:02d}"


def presence_ttl():
    return int(getattr(settings, "CHAT_CALL_PRESENCE_TTL", 12))


def _presence_key(session_id):
    return f"chat:call_presence:{session_id}"


def touch_presence(session_id, user_id, media_state):
    """Refresh *user_id*'s heartbeat. Returns True if media_state changed."""
    key = _presence_key(session_id)
    data = cache.get(key) or {}
    prev = data.get(str(user_id))
    changed = prev != media_state
    data[str(user_id)] = media_state
    cache.set(key, data, presence_ttl())
    return changed


def get_presence(session_id):
    """Return `{user_id_str: media_state}` for users with a fresh heartbeat."""
    return cache.get(_presence_key(session_id)) or {}


def drop_presence(session_id, user_id):
    key = _presence_key(session_id)
    data = cache.get(key)
    if data and str(user_id) in data:
        del data[str(user_id)]
        cache.set(key, data, presence_ttl())
