"""The vault's user settings: the ones kept on the account, not the device."""

from django.utils.translation import pgettext_lazy
from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings

# Every sort the listing offers, for the toolbar, the preferences panel and the
# setting alike. A value is the entry field the browser compares; "default"
# keeps the server's order.
SORT_FIELDS = (
    ("default", pgettext_lazy("vault sort", "Default")),
    ("name", pgettext_lazy("vault sort", "Name")),
    ("favorite", pgettext_lazy("vault sort", "Favorite")),
    ("created", pgettext_lazy("vault sort", "Created")),
    ("modified", pgettext_lazy("vault sort", "Modified")),
)


class VaultSettings(SidebarSettings):
    default_sort = serializers.ChoiceField(choices=SORT_FIELDS)
    # Bounded: a vault that never locks is what a bad value here would cause.
    lock_after_minutes = serializers.IntegerField(min_value=1, max_value=1440)
