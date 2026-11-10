"""Telling the people around a file what just happened to it."""

from django.utils import translation
from django.utils.translation import gettext as _

from workspace.files.queries import can_access
from workspace.notifications.services.notifications import notify
from workspace.users.services.settings import get_user_language


def announce_replaced(instance, actor):
    """Tell live viewers and the owner that *instance* got new content."""
    from workspace.files.sse_provider import push_file_event

    push_file_event(
        instance,
        "file_updated",
        actor.username,
        exclude_user_id=actor.pk,
    )
    if instance.owner_id != actor.pk and can_access(instance.owner, instance):
        with translation.override(get_user_language(instance.owner)):
            notify(
                recipient=instance.owner,
                origin="files",
                title=_('%(actor)s edited "%(name)s"')
                % {"actor": actor.username, "name": instance.name},
                url=f"/files/{instance.parent_id}" if instance.parent_id else "/files",
                actor=actor,
                source=instance,
            )
