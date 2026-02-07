from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def date_label(value):
    """Return 'Today', 'Yesterday', or a short date like 'Feb 5'."""
    if not value:
        return ''
    today = timezone.localdate()
    if value == today:
        return 'Today'
    diff = (today - value).days
    if diff == 1:
        return 'Yesterday'
    # Use %#d on Windows, %-d on Unix for day without leading zero
    try:
        return value.strftime('%b %-d')
    except ValueError:
        return value.strftime('%b %#d')


@register.filter
def format_time(value):
    """Format a datetime as 'HH:MM' (24h)."""
    if not value:
        return ''
    local = timezone.localtime(value)
    return local.strftime('%H:%M')


@register.inclusion_tag('chat/ui/partials/_reactions.html')
def render_reactions(message, current_user):
    """Group reactions by emoji and check if current user reacted."""
    reactions = list(message.reactions.all())
    if not reactions:
        return {'groups': [], 'message_uuid': message.uuid}

    emoji_map = {}
    for r in reactions:
        if r.emoji not in emoji_map:
            emoji_map[r.emoji] = {'emoji': r.emoji, 'count': 0, 'users': [], 'has_mine': False}
        emoji_map[r.emoji]['count'] += 1
        emoji_map[r.emoji]['users'].append(r.user.username)
        if r.user_id == current_user.id:
            emoji_map[r.emoji]['has_mine'] = True

    return {
        'groups': list(emoji_map.values()),
        'message_uuid': message.uuid,
    }
