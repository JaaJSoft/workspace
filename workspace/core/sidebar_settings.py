"""The setting every module with a folding sidebar declares."""

from rest_framework import serializers


class SidebarSettings(serializers.Serializer):
    """Base of a module's settings when its sidebar reduces to an icon rail.

    The key is ``setting_keys.SIDEBAR_COLLAPSED``, stored under the module's
    own name; the shell reads it to size the sidebar before any script runs.
    """

    sidebar_collapsed = serializers.BooleanField()
