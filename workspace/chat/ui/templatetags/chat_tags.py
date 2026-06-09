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


# Emojis offered as one-click reactions in the message hover toolbar. Single
# source of truth: the picker partial renders these server-side, so it stays in
# sync with the reaction bubbles without duplicating the list in JS.
QUICK_REACTION_EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🎉"]


@register.inclusion_tag("chat/ui/partials/_reaction_picker.html")
def render_reaction_picker(message, current_user):
    """Quick-reaction emojis for the hover toolbar, each flagged with whether
    the current user already reacted with it so the picker shows it as selected.

    Reuses the reactions prefetched for `render_reactions`
    (`prefetch_related('reactions__user')`), so it adds no query when both tags
    render the same message.
    """
    mine = {r.emoji for r in message.reactions.all() if r.user_id == current_user.id}
    return {
        "message_uuid": message.uuid,
        "quick_reactions": [
            {"emoji": e, "has_mine": e in mine} for e in QUICK_REACTION_EMOJIS
        ],
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
