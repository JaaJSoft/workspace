"""@username mention parsing and badge rendering, shared across modules."""

import re

from django.utils.html import escape

# A mention starts at the beginning of a line or after whitespace; foo@bar
# (emails, handles inside words) must stay literal text.
_MENTION_RE = re.compile(r"(?:(?<=\s)|(?<=^))@(\w+)", re.MULTILINE)


def extract_mentions(body):
    """Extract @username tokens from body text.

    Returns a set of usernames (excluding 'everyone') and whether @everyone
    was used.
    """
    tokens = set(re.findall(r"@(\w+)", body))
    has_everyone = "everyone" in tokens
    tokens.discard("everyone")
    return tokens, has_everyone


def mention_badge(username, user_id=None):
    """Return the badge HTML for one mention; hover card wired when user_id is known."""
    if username == "everyone":
        return '<span class="mention-badge mention-everyone">@everyone</span>'
    if user_id:
        return (
            f'<span class="mention-badge" data-username="{username}" data-user-id="{user_id}"'
            f' onmouseenter="window._userCardShow(this,{user_id})"'
            f' onmouseleave="window._userCardScheduleHide(this)"'
            f">@{username}</span>"
        )
    return f'<span class="mention-badge" data-username="{username}">@{username}</span>'


def render_comment_body(body, mention_map):
    """Escape a plain-text comment body and turn known @mentions into badges.

    mention_map maps username -> user pk; tokens outside the map stay literal.
    Escaping before substitution is safe because usernames are \\w+ only, so
    escape() never rewrites a matched token. Newlines are kept verbatim (the
    template renders through whitespace-pre-wrap).
    """
    escaped = escape(body)

    def _sub(match):
        username = match.group(1)
        if username in mention_map:
            return mention_badge(username, mention_map[username])
        return match.group(0)

    return _MENTION_RE.sub(_sub, escaped)
