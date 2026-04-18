"""Declarative view-level caching with predictable keys and easy invalidation.

Usage on DRF class-based views::

    from workspace.common.cache import cache_response, invalidate

    class CalendarListView(APIView):
        @cache_response(300)
        def get(self, request): ...

    # In a write path (create/update/delete):
    class CalendarDetailView(APIView):
        def put(self, request, calendar_id):
            ...
            invalidate('CalendarListView', user=request.user)

For global (non-user-specific) caching::

    class ModulesView(APIView):
        @cache_response(3600, per_user=False)
        def get(self, request): ...

    # Invalidation:
    invalidate('ModulesView')
"""

import hashlib
from functools import wraps

from django.core.cache import cache
from rest_framework.response import Response


def _build_key(prefix, request, per_user):
    parts = [f'view:{prefix}']
    if per_user and hasattr(request, 'user') and request.user.is_authenticated:
        parts.append(f'u:{request.user.pk}')
    raw_params = getattr(request, 'query_params', None) or getattr(request, 'GET', {})
    params = raw_params.dict() if hasattr(raw_params, 'dict') else dict(raw_params)
    if params:
        raw = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
        parts.append(f'q:{hashlib.md5(raw.encode()).hexdigest()[:10]}')
    return ':'.join(parts)


def cache_response(timeout=300, per_user=True):
    """Cache a DRF view method's response data.

    Stores ``response.data`` (a Python dict/list) under a predictable key
    built from the view class name, user id, and query parameters.
    Only caches successful (2xx) GET responses.
    """
    def decorator(method):
        @wraps(method)
        def wrapper(self, request, *args, **kwargs):
            if request.method != 'GET':
                return method(self, request, *args, **kwargs)
            prefix = self.__class__.__name__
            key = _build_key(prefix, request, per_user)
            cached = cache.get(key)
            if cached is not None:
                return Response(cached)
            response = method(self, request, *args, **kwargs)
            if 200 <= response.status_code < 300:
                cache.set(key, response.data, timeout)
            return response
        return wrapper
    return decorator


def invalidate(view_name, user=None):
    """Delete the cached response for *view_name*.

    Pass *user* for per-user caches.  Without *user*, deletes the global
    (non-per-user) entry.  Query-param variants expire naturally via TTL.
    """
    parts = [f'view:{view_name}']
    if user is not None:
        uid = user.pk if hasattr(user, 'pk') else user
        parts.append(f'u:{uid}')
    cache.delete(':'.join(parts))
