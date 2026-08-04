"""Resolving @mentions in chat messages against real user accounts."""

from django.contrib.auth import get_user_model

from workspace.common.services.mentions import extract_mentions, resolve_mentions


def build_mention_map(body, users=None):
    """Return (username -> user id) for the mentions in *body*, plus @everyone.

    Candidate tokens are looked up in a single query, then resolved longest
    match first, so the map holds exactly the mentions rendering will turn into
    badges - a shorter prefix that happens to name another account never leaks
    in and never triggers a notification. *users* narrows the pool of
    mentionable accounts (defaults to every user).
    """
    candidates, has_everyone = extract_mentions(body)
    mention_map = {}
    if candidates:
        qs = users if users is not None else get_user_model().objects.all()
        rows = list(qs.filter(username__in=candidates).values_list("id", "username"))
        resolved = resolve_mentions(body, (username for _, username in rows))
        mention_map = {username: uid for uid, username in rows if username in resolved}
    if has_everyone:
        mention_map["everyone"] = None
    return mention_map, has_everyone
