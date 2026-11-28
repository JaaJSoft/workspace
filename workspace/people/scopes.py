"""An address book named in one word: ``mine`` or ``group:<id>``.

The form the People page keeps in its address and its menus, and the one an
import from another cloud is configured with. The API names a book by its
``group`` instead - a relation, null for one's own.
"""

from django.utils.translation import gettext as _
from rest_framework import serializers

SCOPE_MINE = "mine"
SCOPE_GROUP_PREFIX = "group:"


def scope_label(obj):
    """The word for the book *obj* is in: ``mine`` or ``group:<id>``."""
    if obj.owner_id is not None:
        return SCOPE_MINE
    return f"{SCOPE_GROUP_PREFIX}{obj.group_id}"


def parse_scope(user, raw):
    """Turn the word into ``{"owner": user}`` or ``{"group": group}``.

    Raises ``serializers.ValidationError`` on an unknown form or a group the
    user is not a member of - the two look the same from outside on purpose.
    """
    if raw == SCOPE_MINE:
        return {"owner": user}
    if isinstance(raw, str) and raw.startswith(SCOPE_GROUP_PREFIX):
        group_id = raw[len(SCOPE_GROUP_PREFIX) :]
        if group_id.isdigit():
            group = user.groups.filter(pk=int(group_id)).first()
            if group is not None:
                return {"group": group}
    raise serializers.ValidationError(_("Unknown scope."))
