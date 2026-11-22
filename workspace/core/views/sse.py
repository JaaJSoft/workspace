import base64
import logging
import time

import orjson
from django.conf import settings
from django.db import close_old_connections
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone, translation
from django.views.decorators.http import require_GET

from workspace.common.metrics import safe_counter, safe_gauge, safe_histogram

from ..module_guard import is_hidden_from
from ..sse_hub import get_hub
from ..sse_registry import sse_registry

logger = logging.getLogger(__name__)

# All metric names in this file MUST start with "sse_".
_P = "sse"

SSE_CONNECTIONS = safe_gauge(
    f"{_P}_active_connections",
    "Number of active global SSE connections",
    multiprocess_mode="livesum",
)

SSE_EVENTS_EMITTED = safe_counter(
    f"{_P}_events_emitted_total",
    "SSE events sent to clients, by provider and event name",
    ["provider", "event"],
)

SSE_PROVIDER_POLL_DURATION = safe_histogram(
    f"{_P}_provider_poll_duration_seconds",
    "Time spent inside provider.poll() during a single call",
    ["provider"],
)

SSE_FORCED_RECONNECTS = safe_counter(
    f"{_P}_forced_reconnects_total",
    "Streams closed because the server-side connection budget was reached",
    ["transport"],
)

SSE_PUBSUB_MESSAGES = safe_counter(
    f"{_P}_pubsub_messages_total",
    "Provider notifications from Redis Pub/Sub acted on by a stream, "
    "several for one provider counting once",
)

# Force a periodic reconnect so workers/providers cycle and stale state clears.
# Aligned with the nginx ingress proxy-read-timeout (600s); the browser
# auto-reconnects via EventSource and resumes from Last-Event-Id.
_MAX_CONNECTION_SECONDS = 600

# Upper bound on an incoming Last-Event-Id before it is ignored outright.
_MAX_CURSOR_CHARS = 512


def _event_json(event_type, data):
    """One event as the page reads it, on the stream and in a snapshot alike."""
    return orjson.dumps({"event": event_type, "data": data})


def _format_sse(event_type, data, event_id=None):
    """Format an SSE event string.

    Uses a single SSE event type 'sse' with the real event name inside the JSON payload.
    """
    lines = ["event: sse"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {_event_json(event_type, data).decode()}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _shows_another_user(request):
    """Whether the page asking was rendered for someone else than the session.

    A tab outlives its session, and a shared worker reconnects its stream for
    as long as one of its pages is open: after another account signed in, the
    browser sends that account's cookie. The page says who it shows (``user``)
    and is refused what belongs to anyone else.
    """
    shown = request.GET.get("user")
    return shown is not None and shown != str(request.user.pk)


def global_stream(request):
    """Global SSE endpoint that aggregates events from all registered providers."""
    # Ahead of any answer: PresenceMiddleware reads it after the view, and a
    # refusal a worker keeps running into is no more someone's activity than
    # the stream is.
    request._is_sse_stream = True

    if not request.user.is_authenticated or _shows_another_user(request):
        return StreamingHttpResponse("", status=403)

    response = StreamingHttpResponse(
        _event_stream(request),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    response.streaming = True
    response["Content-Encoding"] = "identity"
    return response


def encode_stream_cursor(cursors):
    """Pack the per-provider resume points into one opaque SSE event id.

    The stream multiplexes every provider onto a single ``id:`` field, but the
    browser only ever replays the last one it saw - so an id naming a single
    provider's position would lose every other provider's. The id carries the
    whole map instead, and each provider is handed its own entry back.
    """
    packed = base64.urlsafe_b64encode(orjson.dumps(cursors)).decode()
    return packed.rstrip("=")


def decode_stream_cursor(raw):
    """Unpack a cursor produced by :func:`encode_stream_cursor`.

    Anything else - a truncated header, an id minted before this encoding, a
    hand-crafted query parameter - yields an empty map, which starts every
    provider from scratch rather than failing the connection.
    """
    if not raw or len(raw) > _MAX_CURSOR_CHARS:
        return {}
    try:
        padded = raw + "=" * (-len(raw) % 4)
        cursors = orjson.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}
    if not isinstance(cursors, dict):
        return {}
    return {
        str(slug): str(value)
        for slug, value in cursors.items()
        if isinstance(value, str | int)
    }


def _incoming_cursors(request):
    """Resume points the client sent, by provider slug.

    ``EventSource`` replays the last id in a header on its own reconnects, but
    a stream the page reopens itself (visibility change, bfcache restore)
    starts a brand new ``EventSource`` with no header at all - so sse.js passes
    the same value as a query parameter. The header wins when both are there.
    """
    raw = request.META.get("HTTP_LAST_EVENT_ID") or request.GET.get("last_event_id")
    return decode_stream_cursor(raw)


def _init_providers(user, cursors, failed=None):
    """Instantiate one provider per registered app the user may see.

    The stream is served here, in core, so the preview-module guard never
    judges it: a provider whose module is hidden from the user is skipped.
    ``failed`` collects the slugs of the providers that could not start.
    """
    providers_info = sse_registry.get_all()
    providers = {}
    for slug, info in providers_info.items():
        if is_hidden_from(user, info.provider_cls.__module__):
            continue
        try:
            providers[slug] = info.provider_cls(user, cursors.get(slug))
        except Exception:
            logger.exception("Failed to instantiate SSE provider '%s'", slug)
            if failed is not None:
                failed.append(slug)
    return providers


def _emit(slug, event_name, data, event_id, cursors):
    """Format one provider event, advancing the stream cursor it resumes from."""
    SSE_EVENTS_EMITTED.labels(provider=slug, event=event_name).inc()
    stream_id = None
    if event_id:
        cursors[slug] = str(event_id)
        stream_id = encode_stream_cursor(cursors)
    return _format_sse(f"{slug}.{event_name}", data, stream_id)


def _initial_events(providers, user_id, render, failed=None):
    """Yield ``render(slug, event_name, data, event_id)`` for every initial event.

    A provider that fails, or an event that does not render, costs that
    provider the rest of its events and the others nothing. ``failed``
    collects its slug.
    """
    for slug, provider in providers.items():
        try:
            for event_name, data, event_id in provider.get_initial_events():
                yield render(slug, event_name, data, event_id)
        except Exception:
            logger.exception(
                "Failed to get initial events from SSE provider '%s' for user %s",
                slug,
                user_id,
            )
            if failed is not None:
                failed.append(slug)


def _emit_initial_events(providers, user_id, cursors):
    """Yield initial SSE events from all providers."""

    def render(slug, event_name, data, event_id):
        return _emit(slug, event_name, data, event_id, cursors)

    yield from _initial_events(providers, user_id, render)


def _snapshot_event(slug, event_name, data, event_id):
    return orjson.Fragment(_event_json(f"{slug}.{event_name}", data))


@require_GET
def stream_snapshot(request):
    """The events a stream opens with, for a page that opens none.

    A shared worker holds one stream for all the tabs of a browser (sse.js).
    A tab that joins it later was not there for the initial events - the unread
    counts, the generations under way - and nothing sends them again, so it
    asks for them here. A tab coming back to a stream it had left (bfcache,
    frozen tab) sends the cursor it stopped at and is given what was queued
    since, as a stream of its own reopened there would have given it.

    ``complete`` is false when a provider failed. The page then keeps its
    cursor and asks again: taking the stream's position for its own would skip
    what that provider had for it.
    """
    if not request.user.is_authenticated or _shows_another_user(request):
        return HttpResponse(status=403)

    # A stream builds its events once the middlewares have left: in UTC and in
    # the instance's language. The same here, or a tab joining late would read
    # other values than the stream sent the others.
    with timezone.override(None), translation.override(settings.LANGUAGE_CODE):
        failed = []
        providers = _init_providers(request.user, _incoming_cursors(request), failed)
        events = list(
            _initial_events(providers, request.user.id, _snapshot_event, failed)
        )

    response = HttpResponse(
        orjson.dumps({"events": events, "complete": not failed}),
        content_type="application/json",
    )
    response["Cache-Control"] = "private, no-cache"
    return response


def _poll_provider(slug, provider, cache_value, user_id, cursors):
    """Poll a single provider and yield any SSE events."""
    try:
        # Force-evaluate inside the timer so we measure the actual work even if
        # the provider returns a generator.
        with SSE_PROVIDER_POLL_DURATION.labels(provider=slug).time():
            events = list(provider.poll(cache_value))
        for event_name, data, event_id in events:
            yield _emit(slug, event_name, data, event_id, cursors)
    except Exception:
        logger.exception(
            "SSE provider '%s' poll failed for user %s",
            slug,
            user_id,
        )


def _event_stream(request):
    """Router: use Redis Pub/Sub when available, fall back to cache polling."""
    hub = get_hub()
    if hub is not None:
        yield from _event_stream_pubsub(request, hub)
    else:
        yield from _event_stream_polling(request)


def _event_stream_pubsub(request, hub):
    """Pub/Sub-based SSE generator for near-instant event delivery."""
    user = request.user
    user_id = user.id
    cursors = _incoming_cursors(request)

    providers = _init_providers(user, cursors)

    # Subscribed before the initial events are built: a notification sent
    # meanwhile is then polled right after them instead of being missed.
    subscription = hub.subscribe(user_id)
    SSE_CONNECTIONS.inc()
    try:
        yield from _emit_initial_events(providers, user_id, cursors)

        start_time = time.monotonic()
        last_keepalive = start_time

        while True:
            if time.monotonic() - start_time > _MAX_CONNECTION_SECONDS:
                SSE_FORCED_RECONNECTS.labels(transport="pubsub").inc()
                return

            # A stream spends most of its life waiting here: its database
            # connection goes back to the pool first, or a handful of open
            # tabs would hold every connection of the worker.
            close_old_connections()
            # Block up to 5s waiting for a notification (gevent-friendly)
            notified = subscription.wait(timeout=5)
            now = time.monotonic()

            if now - last_keepalive >= 15:
                yield ":keepalive\n\n"
                last_keepalive = now

            if not notified:
                # Timeout: poll all providers with None (timer-based checks)
                for slug, provider in providers.items():
                    yield from _poll_provider(slug, provider, None, user_id, cursors)
            for slug in notified:
                SSE_PUBSUB_MESSAGES.inc()
                # Targeted: only poll the providers that published
                if slug in providers:
                    yield from _poll_provider(
                        slug, providers[slug], now, user_id, cursors
                    )
    finally:
        subscription.close()
        SSE_CONNECTIONS.dec()


def _event_stream_polling(request):
    """Cache-polling fallback SSE generator for local dev without Redis."""
    from django.core.cache import cache

    user = request.user
    user_id = user.id
    cursors = _incoming_cursors(request)

    providers = _init_providers(user, cursors)

    last_cache_values = {slug: None for slug in providers}

    start_time = time.time()
    last_check = time.time()
    last_keepalive = time.time()

    yield from _emit_initial_events(providers, user_id, cursors)

    SSE_CONNECTIONS.inc()
    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed > _MAX_CONNECTION_SECONDS:
                SSE_FORCED_RECONNECTS.labels(transport="polling").inc()
                return

            now = time.time()

            if now - last_keepalive >= 15:
                yield ":keepalive\n\n"
                last_keepalive = now

            if now - last_check >= 2:
                last_check = now

                for slug, provider in providers.items():
                    try:
                        cache_key = f"sse:{slug}:last_event:{user_id}"
                        cache_value = cache.get(cache_key)

                        # Determine if dirty (cache value changed)
                        changed_value = None
                        if cache_value and cache_value != last_cache_values[slug]:
                            last_cache_values[slug] = cache_value
                            changed_value = cache_value

                        yield from _poll_provider(
                            slug,
                            provider,
                            changed_value,
                            user_id,
                            cursors,
                        )
                    except Exception:
                        logger.exception(
                            "SSE provider '%s' cache check failed for user %s",
                            slug,
                            user_id,
                        )

            # Never wait holding a database connection, as in the Pub/Sub loop.
            close_old_connections()
            time.sleep(1)
    finally:
        SSE_CONNECTIONS.dec()
