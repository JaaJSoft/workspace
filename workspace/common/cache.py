"""Declarative caching with predictable keys and easy invalidation.

Function-level (any service method)::

    from workspace.common.cache import cached, invalidate_tags

    @cached(key=lambda u: f'notif:unread:{u.pk}', ttl=300,
            tags=lambda u: [f'notif:user:{u.pk}'])
    def get_unread_count(user): ...

    def notify(*, recipient, ...):
        ...
        invalidate_tags(f'notif:user:{recipient.id}')

View-level (DRF class-based views)::

    from workspace.common.cache import cached_response, invalidate

    class CalendarListView(APIView):
        @cached_response(300)
        def get(self, request): ...

    class CalendarDetailView(APIView):
        def put(self, request, calendar_id):
            ...
            invalidate('CalendarListView', user=request.user)

For global (non-user-specific) view caching::

    class ModulesView(APIView):
        @cached_response(3600, per_user=False)
        def get(self, request): ...

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


def cached_response(timeout=300, per_user=True):
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


# ── Function-level cache with tag-based invalidation ────────────────────────
#
# ``cached_response`` above caches DRF responses keyed by URL/user/params.
# ``cached`` below caches arbitrary function return values keyed by whatever
# the caller wants; invalidation works through tags. Each tag has a version
# counter, and a cached entry's effective key embeds the version of every tag
# it carries. ``invalidate_tags`` bumps the counters — entries that referenced
# the old version become orphans and expire naturally by TTL.
#
# This avoids the "delete every key matching a pattern" dance (needing Redis
# SCAN or a tag→keys map with race conditions) and works uniformly on LocMem
# and Redis.

_CACHED_MISS = object()
_TAG_VERSION_PREFIX = 'cache:v:'


def _resolve(spec, args, kwargs):
    """Decorator arg accepted as either a value or a callable(*args, **kwargs)."""
    return spec(*args, **kwargs) if callable(spec) else spec


def _tag_version(tag):
    vkey = f'{_TAG_VERSION_PREFIX}{tag}'
    v = cache.get(vkey)
    if v is None:
        cache.set(vkey, 1, None)
        return 1
    return v


def invalidate_tags(*tags):
    """Bump each tag's version counter.

    Every cache entry whose key embeds the old version is now unreachable;
    it will be evicted by TTL. Call this in any write path that changes the
    data a cached reader relies on.
    """
    for tag in tags:
        vkey = f'{_TAG_VERSION_PREFIX}{tag}'
        current = cache.get(vkey) or 1
        cache.set(vkey, current + 1, None)


def cached(*, key, ttl, tags=None):
    """Memoize a function's return value in the Django cache.

    ``key`` and ``tags`` may be a static value or a callable receiving the
    decorated function's args/kwargs. Call ``invalidate_tags(*tags)`` in write
    paths to evict all entries whose key embeds a given tag's current version.

    Example::

        @cached(
            key=lambda user: f'notif:unread:{user.pk}',
            ttl=300,
            tags=lambda user: [f'notif:user:{user.pk}'],
        )
        def get_unread_count(user): ...

        def notify(*, recipient, ...):
            ...
            invalidate_tags(f'notif:user:{recipient.id}')
    """
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            base_key = _resolve(key, args, kwargs)
            tag_list = _resolve(tags, args, kwargs) or []
            if tag_list:
                versions = '|'.join(str(_tag_version(t)) for t in tag_list)
                full_key = f'{base_key}|v:{versions}'
            else:
                full_key = base_key
            hit = cache.get(full_key, _CACHED_MISS)
            if hit is not _CACHED_MISS:
                return hit
            result = fn(*args, **kwargs)
            cache.set(full_key, result, ttl)
            return result
        return wrapped
    return decorator
