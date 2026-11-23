"""The address a request comes from, for the limits counted per address."""

from rest_framework.settings import api_settings
from rest_framework.throttling import BaseThrottle


def client_address(request):
    """The peer address, unless the deployment has said how far to trust
    ``X-Forwarded-For``.

    The header is written by the caller: read without knowing which hop is the
    real peer, a different value per request buys a fresh bucket and the limit
    is gone. Only a declared proxy count (``NUM_PROXIES``) says which entry a
    trusted proxy wrote, so without one the header is ignored rather than
    believed.
    """
    if api_settings.NUM_PROXIES is None:
        return request.META.get("REMOTE_ADDR")
    return BaseThrottle().get_ident(request)
