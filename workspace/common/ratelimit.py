"""Centralised rate-limiting helpers for DRF and Django views."""

from django.conf import settings
from django_ratelimit.exceptions import Ratelimited
from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def get_rate(tier):
    """Return a callable that reads settings.RATE_LIMITS[tier] at request time.

    This allows @override_settings to change rates in tests, because the
    setting is read when the request is processed rather than at import time.

    Usage::

        @ratelimit(key='ip', rate=get_rate('sensitive'), method='POST', block=True)
    """
    def _rate_fn(group, request):
        return settings.RATE_LIMITS[tier]
    _rate_fn.__name__ = f'get_rate_{tier}'
    _rate_fn.__qualname__ = f'get_rate.<locals>.get_rate_{tier}'
    return _rate_fn


def get_client_ip(request):
    """Return the client IP, checking X-Forwarded-For first.

    Used as RATELIMIT_IP_META_KEY callable so django-ratelimit works
    both behind a reverse proxy (X-Forwarded-For present) and in
    development/tests (REMOTE_ADDR only).
    """
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def get_client_key(group, request):
    """Return user PK for authenticated users, IP for anonymous.

    Used as the `key` callable for django-ratelimit decorators.
    Signature: key(group, request) -> str
    """
    if hasattr(request, 'user') and request.user.is_authenticated:
        return str(request.user.pk)
    return get_client_ip(request)


def ratelimit_exception_handler(exc, context):
    """DRF exception handler that returns 429 for Ratelimited exceptions.

    Since Ratelimited is a subclass of Django's PermissionDenied, DRF's
    default handler would convert it to a 403. This handler intercepts
    Ratelimited exceptions and returns a proper 429 response instead.
    """
    if isinstance(exc, Ratelimited):
        response = Response(
            {'detail': 'Too many requests. Please try again later.'},
            status=http_status.HTTP_429_TOO_MANY_REQUESTS,
        )
        response['Retry-After'] = '60'
        return response
    return drf_exception_handler(exc, context)
