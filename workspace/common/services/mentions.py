"""@username mention parsing and badge rendering, shared across modules."""

import re

from django.utils.html import escape

# A mention starts at the beginning of a line or after whitespace; foo@bar
# (emails, handles inside words) must stay literal text. The token charset is
# Django's username charset, so it swallows separators a name may contain -
# _candidate_prefixes resolves the resulting ambiguity.
_MENTION_RE = re.compile(r"(?:(?<=\s)|(?<=^))@([\w.@+-]+)", re.MULTILINE)

# Username characters a name may also end before, so a token can split there.
_PREFIX_BOUNDARIES = ".@+-"


def _candidate_prefixes(token):
    """Yield the prefixes of *token* that could be a username, longest first.

    '@alice.bob' is ambiguous: the user 'alice.bob', or the user 'alice'
    followed by the literal '.bob'. Callers walk this in order and stop at the
    first prefix naming a real user, which makes the longest match win.
    """
    yield token
    for i in range(len(token) - 1, 0, -1):
        if token[i] in _PREFIX_BOUNDARIES:
            yield token[:i]


def extract_mentions(body):
    """Extract candidate @username tokens from body text.

    Returns a set of candidate usernames (excluding 'everyone') and whether
    @everyone was used. A single token contributes every prefix that could name
    a user, so callers can look all of them up at once; feed the survivors back
    to resolve_mentions() to keep only the longest match per token.
    """
    candidates = set()
    for match in _MENTION_RE.finditer(body):
        candidates.update(_candidate_prefixes(match.group(1)))
    has_everyone = "everyone" in candidates
    candidates.discard("everyone")
    return candidates, has_everyone


def resolve_mentions(body, known_usernames):
    """Usernames from *known_usernames* actually mentioned in *body*.

    Each token resolves to at most one user - the longest prefix that is known -
    so notifications match exactly what rendering turns into a badge.
    """
    known = set(known_usernames)
    resolved = set()
    for match in _MENTION_RE.finditer(body):
        for prefix in _candidate_prefixes(match.group(1)):
            if prefix in known:
                resolved.add(prefix)
                break
    return resolved


def mentioned_users(audience, body, actor):
    """Audience members mentioned in *body*, excluding the actor."""
    names = resolve_mentions(body, (u.username for u in audience))
    if not names:
        return []
    return [u for u in audience if u.username in names and u != actor]


def newly_mentioned_users(audience, actor, old_body, new_body):
    """Audience members mentioned in *new_body* but not already in *old_body*."""
    old_names = resolve_mentions(old_body, (u.username for u in audience))
    return [
        u
        for u in mentioned_users(audience, new_body, actor)
        if u.username not in old_names
    ]


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


def substitute_mentions(text, mention_map, wrap=mention_badge):
    """Replace known @mentions in *text* with wrap(username, user_id).

    Only the longest prefix of a token that is in mention_map is replaced; what
    follows (trailing punctuation, an unrelated suffix) stays literal, as does a
    token naming nobody. *wrap* lets callers emit something other than a badge -
    chat swaps in placeholders that survive markdown rendering.
    """

    def _sub(match):
        token = match.group(1)
        for prefix in _candidate_prefixes(token):
            if prefix in mention_map:
                return wrap(prefix, mention_map[prefix]) + token[len(prefix) :]
        return match.group(0)

    return _MENTION_RE.sub(_sub, text)


def render_comment_body(body, mention_map):
    """Escape a plain-text comment body and turn known @mentions into badges.

    mention_map maps username -> user pk; tokens outside the map stay literal.
    Escaping before substitution is safe because no character escape() rewrites
    can appear in a username. Newlines are kept verbatim (the template renders
    through whitespace-pre-wrap).
    """
    return substitute_mentions(escape(body), mention_map)
