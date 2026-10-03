"""Shared querysets for looking people up."""

from django.contrib.auth import get_user_model
from django.db.models import Q

MIN_SEARCH_QUERY_LENGTH = 2
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


def search_people(
    query, requesting_user=None, limit=DEFAULT_SEARCH_LIMIT, *, with_email=False
):
    """Active, non-bot users matching *query* on username, first or last name.

    Bots are excluded on purpose: every caller is a person picker, and an
    assistant offered as a colleague is never the right answer. *requesting_user*
    is dropped from the results when given - you don't pick yourself.
    *with_email* also matches the email and drops users who have none: what a
    recipient picker needs, since it can only offer an address.
    """
    User = get_user_model()
    match = (
        Q(username__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
    )
    qs = User.objects.filter(is_active=True, bot_profile__isnull=True)
    if with_email:
        match |= Q(email__icontains=query)
        qs = qs.exclude(email="")
    qs = qs.filter(match)
    if getattr(requesting_user, "pk", None):
        qs = qs.exclude(pk=requesting_user.pk)
    return qs.order_by("username")[:limit]


def active_user_with_email(email):
    """The active, non-bot user whose email is ``email`` (case-insensitive), or ``None``."""
    email = (email or "").strip()
    if not email:
        return None
    return (
        get_user_model()
        .objects.filter(email__iexact=email, is_active=True, bot_profile__isnull=True)
        .order_by("pk")
        .first()
    )
