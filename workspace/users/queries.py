"""Shared querysets for looking people up."""

from django.contrib.auth import get_user_model
from django.db.models import Q

MIN_SEARCH_QUERY_LENGTH = 2
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


def search_people(query, requesting_user=None, limit=DEFAULT_SEARCH_LIMIT):
    """Active, non-bot users matching *query* on username, first or last name.

    Bots are excluded on purpose: every caller is a person picker, and an
    assistant offered as a colleague is never the right answer. *requesting_user*
    is dropped from the results when given - you don't pick yourself.
    """
    User = get_user_model()
    qs = User.objects.filter(
        Q(username__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query),
        is_active=True,
        bot_profile__isnull=True,
    )
    if getattr(requesting_user, "pk", None):
        qs = qs.exclude(pk=requesting_user.pk)
    return qs.order_by("username")[:limit]
