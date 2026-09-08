"""A per-window counter for simple cache-backed rate limiting.

``cache.add`` + ``cache.incr`` instead of a get-then-set pair: two
concurrent requests both reading the same count and both writing the same
next value would silently admit one extra attempt past the limit, since
neither read ever saw the other's write.

``incr`` can still raise ``ValueError`` if the key is gone by the time it
runs - expired exactly at the TTL boundary between the two calls, or
evicted under memory pressure (the likelier trigger against a real cache
with a memory cap) - and that must not surface as an uncaught 500 on an
endpoint an anonymous caller can hit at will. Both django-redis and Django's
LocMemCache raise on an ``incr`` of a missing key.
"""

from django.core.cache import cache


def increment_counter(key, ttl_seconds) -> int:
    """Increment the counter at *key*, creating it at 0 first if needed.

    Returns the counter's new value. A key that vanished between the add
    and the incr restarts at 1 rather than raising - undercounting by one
    request is the safe direction for a rate limit to fail in.
    """
    cache.add(key, 0, ttl_seconds)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl_seconds)
        return 1
