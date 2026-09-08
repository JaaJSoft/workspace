"""Chat module: voice messages and WebRTC calls."""

import os
from datetime import timedelta

from workspace.chat.services.webrtc import build_ice_servers

# Hard ceiling for a recorded voice message, enforced both by the browser
# (auto-stop) and by the message endpoint.
CHAT_VOICE_MAX_SECONDS = 300

# ICE servers (STUN/TURN) for chat calls. See workspace.chat.services.webrtc for the
# env-var format. Configured here so TURN can be added later without code changes.
CHAT_CALL_ICE_SERVERS = build_ice_servers()
CHAT_CALL_MAX_PARTICIPANTS = int(os.getenv("CHAT_CALL_MAX_PARTICIPANTS", "6"))
CHAT_CALL_PRESENCE_TTL = int(os.getenv("CHAT_CALL_PRESENCE_TTL", "12"))

# Meetings: how long before an occurrence the lobby opens, how long after it
# stays reachable, and the fallback length for an event with no end.
MEETING_LOBBY_LEAD = timedelta(
    minutes=int(os.getenv("MEETING_LOBBY_LEAD_MINUTES", "15"))
)
MEETING_GRACE = timedelta(minutes=int(os.getenv("MEETING_GRACE_MINUTES", "30")))
MEETING_DEFAULT_DURATION = timedelta(minutes=60)
# Per-IP rate limiting cannot stop a distributed flood from burying a host's
# lobby mid-call, so the number of WAITING rows per meeting is capped too.
MEETING_MAX_WAITING_GUESTS = int(os.getenv("MEETING_MAX_WAITING_GUESTS", "20"))
