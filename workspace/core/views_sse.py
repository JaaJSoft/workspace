import time
import logging

import orjson

from django.http import StreamingHttpResponse
from prometheus_client import Gauge

from .sse_registry import sse_registry

logger = logging.getLogger(__name__)

SSE_CONNECTIONS = Gauge(
    'sse_active_connections',
    'Number of active global SSE connections',
)


def _format_sse(event_type, data, event_id=None):
    """Format an SSE event string.

    Uses a single SSE event type 'sse' with the real event name inside the JSON payload.
    """
    payload = {
        'event': event_type,
        'data': data,
    }
    lines = ['event: sse']
    if event_id:
        lines.append(f'id: {event_id}')
    lines.append(f'data: {orjson.dumps(payload).decode()}')
    lines.append('')
    lines.append('')
    return '\n'.join(lines)


def global_stream(request):
    """Global SSE endpoint that aggregates events from all registered providers."""
    if not request.user.is_authenticated:
        return StreamingHttpResponse('', status=403)

    request._is_sse_stream = True

    response = StreamingHttpResponse(
        _event_stream(request),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache, no-transform'
    response['X-Accel-Buffering'] = 'no'
    response.streaming = True
    response['Content-Encoding'] = 'identity'
    return response


def _init_providers(user, last_event_id):
    """Instantiate one provider per registered app."""
    providers_info = sse_registry.get_all()
    providers = {}
    for slug, info in providers_info.items():
        try:
            providers[slug] = info.provider_cls(user, last_event_id)
        except Exception:
            logger.exception("Failed to instantiate SSE provider '%s'", slug)
    return providers


def _emit_initial_events(providers, user_id):
    """Yield initial SSE events from all providers."""
    for slug, provider in providers.items():
        try:
            for event_name, data, event_id in provider.get_initial_events():
                yield _format_sse(f'{slug}.{event_name}', data, event_id)
        except Exception:
            logger.exception(
                "Failed to get initial events from SSE provider '%s' for user %s",
                slug, user_id,
            )


def _poll_provider(slug, provider, cache_value, user_id):
    """Poll a single provider and yield any SSE events."""
    try:
        events = provider.poll(cache_value)
        for event_name, data, event_id in events:
            yield _format_sse(
                f'{slug}.{event_name}', data, event_id,
            )
    except Exception:
        logger.exception(
            "SSE provider '%s' poll failed for user %s",
            slug, user_id,
        )


def _event_stream(request):
    """Router: use Redis Pub/Sub when available, fall back to cache polling."""
    try:
        from django_redis import get_redis_connection
        redis = get_redis_connection('default')
    except Exception:
        redis = None

    if redis is not None:
        yield from _event_stream_pubsub(request, redis)
    else:
        yield from _event_stream_polling(request)


def _event_stream_pubsub(request, redis):
    """Pub/Sub-based SSE generator for near-instant event delivery."""
    user = request.user
    user_id = user.id
    last_event_id = request.META.get('HTTP_LAST_EVENT_ID')

    providers = _init_providers(user, last_event_id)

    yield from _emit_initial_events(providers, user_id)

    pubsub = redis.pubsub()
    pubsub.subscribe(f'sse:user:{user_id}')

    SSE_CONNECTIONS.inc()
    try:
        start_time = time.monotonic()
        last_keepalive = start_time

        while True:
            if time.monotonic() - start_time > 60:
                return

            # Block up to 5s waiting for message (gevent-friendly)
            message = pubsub.get_message(timeout=5)
            now = time.monotonic()

            # Keepalive every 15s
            if now - last_keepalive >= 15:
                yield ':keepalive\n\n'
                last_keepalive = now

            if message is None:
                # Timeout: poll all providers with None (timer-based checks)
                for slug, provider in providers.items():
                    yield from _poll_provider(slug, provider, None, user_id)
            elif message['type'] == 'message':
                try:
                    # Targeted: only poll the provider that published
                    data = orjson.loads(message['data'])
                    slug = data['provider']
                    if slug in providers:
                        yield from _poll_provider(
                            slug, providers[slug], time.monotonic(), user_id,
                        )
                except Exception:
                    logger.exception(
                        "Failed to process Pub/Sub message for user %s",
                        user_id,
                    )
    finally:
        pubsub.unsubscribe(f'sse:user:{user_id}')
        pubsub.close()
        SSE_CONNECTIONS.dec()


def _event_stream_polling(request):
    """Cache-polling fallback SSE generator for local dev without Redis."""
    from django.core.cache import cache

    user = request.user
    user_id = user.id
    last_event_id = request.META.get('HTTP_LAST_EVENT_ID')

    providers = _init_providers(user, last_event_id)

    # Track last cache value per provider
    last_cache_values = {slug: None for slug in providers}

    start_time = time.time()
    last_check = time.time()
    last_keepalive = time.time()

    yield from _emit_initial_events(providers, user_id)

    SSE_CONNECTIONS.inc()
    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed > 60:
                return

            now = time.time()

            # Keepalive every 15 seconds
            if now - last_keepalive >= 15:
                yield ':keepalive\n\n'
                last_keepalive = now

            # Poll every 2 seconds
            if now - last_check >= 2:
                last_check = now

                for slug, provider in providers.items():
                    try:
                        cache_key = f'sse:{slug}:last_event:{user_id}'
                        cache_value = cache.get(cache_key)

                        # Determine if dirty (cache value changed)
                        changed_value = None
                        if cache_value and cache_value != last_cache_values[slug]:
                            last_cache_values[slug] = cache_value
                            changed_value = cache_value

                        yield from _poll_provider(
                            slug, provider, changed_value, user_id,
                        )
                    except Exception:
                        logger.exception(
                            "SSE provider '%s' cache check failed for user %s",
                            slug, user_id,
                        )

            time.sleep(1)
    finally:
        SSE_CONNECTIONS.dec()
