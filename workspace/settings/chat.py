"""Chat module: voice messages and WebRTC calls."""

import os

from .env import env_list

# Hard ceiling for a recorded voice message, enforced both by the browser
# (auto-stop) and by the message endpoint.
CHAT_VOICE_MAX_SECONDS = 300

# STUN/TURN URLs for chat calls. Set but empty means no ICE server at all.
CHAT_CALL_ICE_SERVERS = (
    env_list("CHAT_CALL_ICE_SERVERS")
    if "CHAT_CALL_ICE_SERVERS" in os.environ
    else ["stun:stun.l.google.com:19302"]
)
# Static TURN pair, shared by every user. Ignored when CHAT_CALL_TURN_SECRET is set.
CHAT_CALL_TURN_USERNAME = os.getenv("CHAT_CALL_TURN_USERNAME", "")
CHAT_CALL_TURN_CREDENTIAL = os.getenv("CHAT_CALL_TURN_CREDENTIAL", "")
# coturn static-auth-secret: each user gets a credential derived from it that
# expires after CHAT_CALL_TURN_TTL seconds (workspace.chat.services.webrtc).
CHAT_CALL_TURN_SECRET = os.getenv("CHAT_CALL_TURN_SECRET", "")
CHAT_CALL_TURN_TTL = int(os.getenv("CHAT_CALL_TURN_TTL", "21600"))
CHAT_CALL_MAX_PARTICIPANTS = int(os.getenv("CHAT_CALL_MAX_PARTICIPANTS", "6"))
CHAT_CALL_PRESENCE_TTL = int(os.getenv("CHAT_CALL_PRESENCE_TTL", "12"))
