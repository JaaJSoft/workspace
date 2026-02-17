import json
import time
import uuid as _uuid
import logging

from django.core.cache import cache
from django.http import StreamingHttpResponse
from prometheus_client import Gauge

from .sse_registry import sse_registry

logger = logging.getLogger(__name__)

SSE_CONNECTIONS = Gauge(
    'sse_active_connections',
    'Number of active global SSE connections',
)


class _SSEEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, _uuid.UUID):
            return str(obj)
        return super().default(obj)


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
    lines.append(f'data: {json.dumps(payload, cls=_SSEEncoder)}')
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


def _event_stream(request):
    user = request.user
    user_id = user.id
    last_event_id = request.META.get('HTTP_LAST_EVENT_ID')

    # Instantiate one provider per registered app
    providers_info = sse_registry.get_all()
    providers = {}
    for slug, info in providers_info.items():
        try:
            providers[slug] = info.provider_cls(user, last_event_id)
        except Exception:
            logger.exception("Failed to instantiate SSE provider '%s'", slug)

    # Track last cache value per provider
    last_cache_values = {slug: None for slug in providers}

    start_time = time.time()
    last_check = time.time()
    last_keepalive = time.time()

    # Send initial events from all providers
    for slug, provider in providers.items():
        try:
            for event_name, data, event_id in provider.get_initial_events():
                yield _format_sse(f'{slug}.{event_name}', data, event_id)
        except Exception:
            logger.exception(
                "Failed to get initial events from SSE provider '%s' for user %s",
                slug, user_id,
            )

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

                        events = provider.poll(changed_value)
                        for event_name, data, event_id in events:
                            yield _format_sse(
                                f'{slug}.{event_name}', data, event_id,
                            )
                    except Exception:
                        logger.exception(
                            "SSE provider '%s' poll failed for user %s",
                            slug, user_id,
                        )

            time.sleep(1)
    finally:
        SSE_CONNECTIONS.dec()
