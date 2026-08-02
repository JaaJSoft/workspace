import json

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def date_label(value):
    """Return 'Today', 'Yesterday', or a short date like 'Feb 5'."""
    if not value:
        return ""
    today = timezone.localdate()
    if value == today:
        return "Today"
    diff = (today - value).days
    if diff == 1:
        return "Yesterday"
    # Use %#d on Windows, %-d on Unix for day without leading zero
    try:
        return value.strftime("%b %-d")
    except ValueError:
        return value.strftime("%b %#d")


@register.filter
def format_time(value):
    """Format a datetime as 'HH:MM' (24h)."""
    if not value:
        return ""
    local = timezone.localtime(value)
    return local.strftime("%H:%M")


@register.inclusion_tag("chat/ui/partials/_read_receipt.html")
def render_read_receipt(message, conversation_kind):
    """Render read receipt indicator for own messages."""
    read_count = getattr(message, "read_count", None)
    if read_count is None:
        return {"show": False}

    return {
        "show": True,
        "read_count": read_count,
        "total_recipients": message.total_recipients,
        "all_read": message.all_read,
        "is_dm": conversation_kind == "dm",
        "message_uuid": message.uuid,
        "conversation_uuid": message.conversation_id,
    }


@register.inclusion_tag("chat/ui/partials/_reaction_picker.html")
def render_reaction_picker(message, current_user, quick_emojis):
    """Quick-reaction emojis for the hover toolbar, each flagged with whether
    the current user already reacted with it so the picker shows it as selected.

    `quick_emojis` is the per-user list computed once per render by the view
    (see workspace.chat.services.reactions.quick_reactions_for); this tag only
    adds the per-message has_mine flag.

    Like render_reactions, callers MUST `prefetch_related('reactions__user')`
    on the message queryset, otherwise iterating `message.reactions.all()` hits
    the DB once per message. message_group.html relies on this: it is rendered
    from `conversation_messages_view`, whose queryset already prefetches
    `reactions__user`.
    """
    mine = {r.emoji for r in message.reactions.all() if r.user_id == current_user.id}
    return {
        "message_uuid": message.uuid,
        "quick_reactions": [{"emoji": e, "has_mine": e in mine} for e in quick_emojis],
    }


@register.inclusion_tag("chat/ui/partials/_reactions.html")
def render_reactions(message, current_user):
    """Group reactions by emoji and check if current user reacted.

    Callers MUST `prefetch_related('reactions__user')` on the message
    queryset, otherwise iterating reactions hits the DB once per row to
    resolve `r.user.username`. The two main view sites (chat
    `conversation_messages_view` and the SSE provider) already do this.
    """
    reactions = list(message.reactions.all())
    if not reactions:
        return {"groups": [], "message_uuid": message.uuid}

    emoji_map = {}
    for r in reactions:
        if r.emoji not in emoji_map:
            emoji_map[r.emoji] = {
                "emoji": r.emoji,
                "count": 0,
                "users": [],
                "has_mine": False,
            }
        emoji_map[r.emoji]["count"] += 1
        emoji_map[r.emoji]["users"].append(r.user.username)
        if r.user_id == current_user.id:
            emoji_map[r.emoji]["has_mine"] = True

    return {
        "groups": list(emoji_map.values()),
        "message_uuid": message.uuid,
    }


def _display_args(parsed):
    """Stringify parsed tool arguments as (key, value) pairs for display."""
    if not isinstance(parsed, dict):
        return []
    pairs = []
    for key, value in parsed.items():
        if isinstance(value, str):
            pairs.append((key, value))
        else:
            pairs.append((key, json.dumps(value, ensure_ascii=False)))
    return pairs


def _pretty_result(content):
    """Pretty-print JSON tool results; leave plain text (incl. truncated JSON) as-is."""
    if not content:
        return ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError, TypeError:
        return content
    if isinstance(parsed, dict | list):
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    return content


@register.inclusion_tag("chat/ui/partials/_tool_calls.html")
def render_tool_calls(message):
    """Flatten Message.tool_data rounds into displayable tool call rows.

    tool_data is overloaded: AI messages store a list of rounds, call system
    messages store a dict - only the list shape is rendered here.
    """
    from workspace.ai.tool_registry import tool_registry

    tool_data = getattr(message, "tool_data", None)
    if not isinstance(tool_data, list):
        return {"calls": []}

    calls = []
    for td_round in tool_data:
        if not isinstance(td_round, dict):
            continue
        results = td_round.get("results")
        if not isinstance(results, list):
            results = []
        results_by_id = {
            r.get("tool_call_id"): r.get("content") or ""
            for r in results
            if isinstance(r, dict)
        }
        tool_calls = td_round.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            function = tc.get("function") or {}
            name = function.get("name") or ""
            raw_args = function.get("arguments") or ""
            try:
                parsed = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError, TypeError:
                parsed = None
            badge = tool_registry.get_badge(name)
            detail = (
                tool_registry.get_detail(name, parsed)
                if isinstance(parsed, dict)
                else ""
            )
            result = results_by_id.get(tc.get("id"), "")
            calls.append(
                {
                    "icon": badge["icon"],
                    "label": badge["label"],
                    "detail": detail,
                    "args": _display_args(parsed),
                    "args_raw": raw_args if parsed is None else "",
                    "result": _pretty_result(result),
                    "is_error": result.startswith(("Error", "Unknown tool")),
                }
            )
    return {"calls": calls}
