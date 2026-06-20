"""Helpers for WebRTC configuration (chat calls)."""

import os

DEFAULT_STUN_URL = "stun:stun.l.google.com:19302"


def build_ice_servers():
    """Build the WebRTC ICE server list from environment variables.

    ``CHAT_CALL_ICE_SERVERS`` is a comma-separated list of URLs, e.g.
    ``"stun:stun.l.google.com:19302,turn:turn.example.com:3478"``, so a TURN
    server can be added later without code changes. STUN needs no credentials;
    for TURN, set ``CHAT_CALL_TURN_USERNAME`` / ``CHAT_CALL_TURN_CREDENTIAL`` and
    they are attached to every ``turn:``/``turns:`` URL (one shared credential
    set).

    Returns a list of RTCIceServer dicts, e.g.
    ``[{"urls": "..."}, {"urls": "turn:...", "username": "...", "credential": "..."}]``.
    """
    raw = os.getenv("CHAT_CALL_ICE_SERVERS", DEFAULT_STUN_URL)
    username = os.getenv("CHAT_CALL_TURN_USERNAME", "")
    credential = os.getenv("CHAT_CALL_TURN_CREDENTIAL", "")

    servers = []
    for url in (part.strip() for part in raw.split(",")):
        if not url:
            continue
        server = {"urls": url}
        if url.startswith(("turn:", "turns:")) and username:
            server["username"] = username
            server["credential"] = credential
        servers.append(server)
    return servers
