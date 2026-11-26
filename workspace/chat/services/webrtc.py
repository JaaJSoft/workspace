"""ICE server configuration for chat calls."""

import base64
import hashlib
import hmac

from django.conf import settings
from django.utils import timezone

TURN_SCHEMES = ("turn:", "turns:")


def turn_credentials(user):
    """The TURN username and credential to hand ``user``, or empty strings.

    With ``CHAT_CALL_TURN_SECRET`` set, they follow coturn's ``use-auth-secret``
    scheme: the username is ``<expiry timestamp>:<user id>`` and the credential
    the base64 HMAC-SHA1 of that username under the secret, so the TURN server
    refuses it on its own once ``CHAT_CALL_TURN_TTL`` seconds have passed.
    Without it, the static pair is returned to everyone.
    """
    secret = settings.CHAT_CALL_TURN_SECRET
    if not secret:
        return settings.CHAT_CALL_TURN_USERNAME, settings.CHAT_CALL_TURN_CREDENTIAL
    expires_at = int(timezone.now().timestamp()) + settings.CHAT_CALL_TURN_TTL
    username = f"{expires_at}:{user.pk}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


def ice_servers_for(user):
    """The RTCIceServer list for a call ``user`` is starting, joining or testing.

    Credentials go on ``turn:``/``turns:`` URLs only; STUN needs none.
    """
    username, credential = turn_credentials(user)
    servers = []
    for url in settings.CHAT_CALL_ICE_SERVERS:
        server = {"urls": url}
        if username and url.startswith(TURN_SCHEMES):
            server["username"] = username
            server["credential"] = credential
        servers.append(server)
    return servers
