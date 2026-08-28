"""Chat module: voice messages and WebRTC calls."""

import os

from workspace.chat.services.webrtc import build_ice_servers

# Hard ceiling for a recorded voice message, enforced both by the browser
# (auto-stop) and by the message endpoint.
CHAT_VOICE_MAX_SECONDS = 300

# ICE servers (STUN/TURN) for chat calls. See workspace.chat.services.webrtc for the
# env-var format. Configured here so TURN can be added later without code changes.
CHAT_CALL_ICE_SERVERS = build_ice_servers()
CHAT_CALL_MAX_PARTICIPANTS = int(os.getenv("CHAT_CALL_MAX_PARTICIPANTS", "6"))
CHAT_CALL_PRESENCE_TTL = int(os.getenv("CHAT_CALL_PRESENCE_TTL", "12"))
