"""The vault's user settings: the ones kept on the account, not the device."""

from rest_framework import serializers

from workspace.core.sidebar_settings import SidebarSettings


class VaultSettings(SidebarSettings):
    default_sort = serializers.ChoiceField(
        choices=("default", "name", "favorite", "created")
    )
    # Bounded: a vault that never locks is what a bad value here would cause.
    lock_after_minutes = serializers.IntegerField(min_value=1, max_value=1440)
