"""Wire format for the server-sent-event streams.

One dialect, shared: the global stream in ``core.views.sse`` and the meeting
guest stream in ``chat.services.guest_stream`` both format their frames here,
so a client parsing one parses the other. Formatting is the only thing in
common between the two - the budgets, cadences and gates are each stream's
own.
"""

import orjson


def format_sse(event_type, data, event_id=None):
    """Format an SSE event string.

    Uses a single SSE event type 'sse' with the real event name inside the JSON payload.
    """
    payload = {
        "event": event_type,
        "data": data,
    }
    lines = ["event: sse"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {orjson.dumps(payload).decode()}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)
