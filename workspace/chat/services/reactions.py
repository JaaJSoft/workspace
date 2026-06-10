from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from workspace.common.cache import cached, invalidate_tags

from ..models import Reaction

# Emojis used to pad the quick-reaction bar when the user has little or no
# history. Single source of truth for the default set.
DEFAULT_QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🎉"]

QUICK_REACTIONS_LIMIT = 6
QUICK_REACTIONS_WINDOW_DAYS = 30
QUICK_REACTIONS_CACHE_TTL = 60 * 60  # 1 hour safety net; invalidated on write


def _user_tag(user_id) -> str:
    return f"chat:quick_reactions:{user_id}"


@cached(
    key=lambda user: f"chat:quick_reactions:{user.id}",
    ttl=QUICK_REACTIONS_CACHE_TTL,
    tags=lambda user: [_user_tag(user.id)],
)
def quick_reactions_for(user) -> list:
    """Ordered list of QUICK_REACTIONS_LIMIT emojis for the hover toolbar.

    The user's most-used reaction emojis over the last
    QUICK_REACTIONS_WINDOW_DAYS days, padded with DEFAULT_QUICK_REACTIONS and
    deduplicated. Cached per user; invalidate via invalidate_quick_reactions on
    reaction add/remove.
    """
    return _compute_quick_reactions(user)


def _compute_quick_reactions(user) -> list:
    cutoff = timezone.now() - timedelta(days=QUICK_REACTIONS_WINDOW_DAYS)
    top = (
        Reaction.objects.filter(user=user, created_at__gte=cutoff)
        .values("emoji")
        .annotate(c=Count("emoji"))
        .order_by("-c", "emoji")  # deterministic order on ties
        .values_list("emoji", flat=True)
    )
    ordered = list(top)
    for emoji in DEFAULT_QUICK_REACTIONS:
        if emoji not in ordered:
            ordered.append(emoji)
    return ordered[:QUICK_REACTIONS_LIMIT]


def invalidate_quick_reactions(user_id) -> None:
    invalidate_tags(_user_tag(user_id))
