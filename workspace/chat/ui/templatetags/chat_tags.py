import json

from django import template
from django.utils import timezone

from workspace.chat.services.participant_keys import user_key
from workspace.chat.services.tool_calls import display_args

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


@register.filter
def shell_attachment_data(message):
    """Attachment payload for the <chat-message-group> shell element.

    Rendered with |json_script inside the bubble child of message_group.html;
    the shell builds the media mosaic and file chips from it client-side.
    Audio attachments are absent on purpose: they render server-side as
    chatAudioPlayer components and ride along as data-part="audio" children.
    """
    return {
        "media": [
            {
                "uuid": str(a.uuid),
                "name": a.original_name,
                "type": a.type,
                "is_image": a.is_image,
            }
            for a in message.media_attachments
        ],
        "files": [
            {
                "uuid": str(a.uuid),
                "name": a.original_name,
                "type": a.type,
                "size": a.size,
            }
            for a in message.file_attachments
        ],
    }


@register.inclusion_tag("chat/ui/partials/_read_receipt.html")
def render_read_receipt(message, conversation_kind, viewer):
    """Render read receipt indicator for own messages.

    A viewer without ``can_see_receipts`` gets nothing: the popover behind it
    lists the conversation's members and is addressed by conversation uuid,
    neither of which a meeting guest may reach.
    """
    read_count = getattr(message, "read_count", None)
    if read_count is None or not viewer.can_see_receipts:
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


def _reacted_emojis(message, viewer):
    """The emojis *viewer* already put on *message*.

    Only a member can react - Reaction has a user column and no guest one -
    so a guest viewer's participant key matches nothing here, which is the
    answer we want rather than a special case.
    """
    return {
        r.emoji
        for r in message.reactions.all()
        if user_key(r.user_id) == viewer.participant_key
    }


@register.inclusion_tag("chat/ui/partials/_reaction_picker.html")
def render_reaction_picker(message, viewer, quick_emojis):
    """Quick-reaction emojis for the hover toolbar, each flagged with whether
    the viewer already reacted with it so the picker shows it as selected.

    `quick_emojis` is the per-user list computed once per render by the view
    (see workspace.chat.services.reactions.quick_reactions_for); this tag only
    adds the per-message has_mine flag.

    Like render_reactions, callers MUST `prefetch_related('reactions__user')`
    on the message queryset, otherwise iterating `message.reactions.all()` hits
    the DB once per message. message_group.html relies on this: it is rendered
    from `conversation_messages_view`, whose queryset already prefetches
    `reactions__user`.
    """
    if not viewer.can_react:
        return {"message_uuid": message.uuid, "quick_reactions": []}
    mine = _reacted_emojis(message, viewer)
    return {
        "message_uuid": message.uuid,
        "quick_reactions": [{"emoji": e, "has_mine": e in mine} for e in quick_emojis],
    }


@register.inclusion_tag("chat/ui/partials/_reactions.html")
def render_reactions(message, viewer):
    """Group reactions by emoji and check if the viewer reacted.

    Callers MUST `prefetch_related('reactions__user')` on the message
    queryset, otherwise iterating reactions hits the DB once per row to
    resolve `r.user.username`. The two main view sites (chat
    `conversation_messages_view` and the SSE provider) already do this.

    A viewer without `can_react` still sees the chips - who reacted with what
    is part of reading the conversation - but the template leaves out the
    toggle, since the endpoint behind it would refuse them.
    """
    reactions = list(message.reactions.all())
    if not reactions:
        return {
            "groups": [],
            "message_uuid": message.uuid,
            "can_react": viewer.can_react,
        }

    mine = _reacted_emojis(message, viewer)
    emoji_map = {}
    for r in reactions:
        if r.emoji not in emoji_map:
            emoji_map[r.emoji] = {
                "emoji": r.emoji,
                "count": 0,
                "users": [],
                "has_mine": r.emoji in mine,
            }
        emoji_map[r.emoji]["count"] += 1
        emoji_map[r.emoji]["users"].append(r.user.username)

    return {
        "groups": list(emoji_map.values()),
        "message_uuid": message.uuid,
        "can_react": viewer.can_react,
    }


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


@register.inclusion_tag("chat/ui/partials/_ai_steps.html")
def render_ai_steps(message):
    """Flatten Message.tool_data rounds into a chronological step timeline.

    tool_data is overloaded: AI messages store a list of rounds, call system
    messages store a dict - only the list shape is rendered here. Step types:
    'thinking' (model reasoning), 'text' (intermediate assistant text emitted
    between tool rounds), 'tool' (one executed call).
    """
    from workspace.ai.tool_registry import tool_registry

    tool_data = getattr(message, "tool_data", None)
    if not isinstance(tool_data, list):
        return {
            "steps": [],
            "tool_count": 0,
            "has_reasoning": False,
            "collapsed": False,
        }

    steps = []
    for td_round in tool_data:
        if not isinstance(td_round, dict):
            continue
        thinking = td_round.get("thinking")
        if isinstance(thinking, str) and thinking.strip():
            steps.append({"type": "thinking", "text": thinking.strip()})
        assistant_content = td_round.get("assistant_content")
        if isinstance(assistant_content, str) and assistant_content.strip():
            steps.append({"type": "text", "text": assistant_content.strip()})
        results = td_round.get("results")
        if not isinstance(results, list):
            results = []
        results_by_id = {
            r.get("tool_call_id"): r.get("content")
            if isinstance(r.get("content"), str)
            else ""
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
            steps.append(
                {
                    "type": "tool",
                    "icon": badge["icon"],
                    "label": badge["label"],
                    "detail": detail,
                    "args": display_args(parsed),
                    "args_raw": raw_args if parsed is None else "",
                    "result": _pretty_result(result),
                    "is_error": result.startswith(("Error:", "Unknown tool:")),
                }
            )

    tool_count = sum(1 for s in steps if s["type"] == "tool")
    has_reasoning = any(s["type"] in ("thinking", "text") for s in steps)
    return {
        "steps": steps,
        "tool_count": tool_count,
        "has_reasoning": has_reasoning,
        "collapsed": has_reasoning or len(steps) > 3,
    }
