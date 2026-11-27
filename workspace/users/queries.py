"""Shared querysets for looking people and groups up."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q

from .models import GroupVisibility

MIN_SEARCH_QUERY_LENGTH = 2
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


def pickable_users():
    """Accounts a person can be picked from: active and not a bot.

    An assistant is never the colleague, contact or member a picker is after,
    so anything that takes a user id from a picker validates it against this.
    """
    return get_user_model().objects.filter(is_active=True, bot_profile__isnull=True)


def nameable_groups(user, *, already_named=None):
    """The groups *user* may name: list in a picker, share with, attach.

    Every group except the hidden ones *user* is not in. A group outside
    this set answers exactly like one that does not exist, so a write path
    resolves the id it was handed against this queryset, not ``Group.objects``.

    *already_named* is a queryset of group ids the object being edited
    already carries. Keeping or removing such a group names nothing new, and
    the owner of a share may have left a hidden group since they added it.
    """
    hidden = GroupVisibility.objects.filter(is_hidden=True).values("group_id")
    allowed = ~Q(pk__in=hidden) | Q(pk__in=user.groups.values("pk"))
    if already_named is not None:
        allowed |= Q(pk__in=already_named)
    return Group.objects.filter(allowed)


def search_people(
    query, requesting_user=None, limit=DEFAULT_SEARCH_LIMIT, *, with_email=False
):
    """Active, non-bot users matching *query* on username, first or last name,
    ignoring accents and case.

    Bots are excluded on purpose: every caller is a person picker, and an
    assistant offered as a colleague is never the right answer. *requesting_user*
    is dropped from the results when given - you don't pick yourself.
    *with_email* also matches the email and drops users who have none: what a
    recipient picker needs, since it can only offer an address.
    """
    match = (
        Q(username__ficontains=query)
        | Q(first_name__ficontains=query)
        | Q(last_name__ficontains=query)
    )
    qs = pickable_users()
    if with_email:
        match |= Q(email__ficontains=query)
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
    return pickable_users().filter(email__iexact=email).order_by("pk").first()
