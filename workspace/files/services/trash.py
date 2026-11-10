"""Trashing, restoring and purging one node, on a user's behalf.

Every path that does one of these for a user comes here, so the action
registry, the lock and the notifications are asked once. A refusal is a
:class:`~.refusals.Refused`.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext

from workspace.notifications.services.notifications import notify_many
from workspace.users.services.settings import in_recipient_languages

from .files import FileService
from .refusals import refusals, require_action, require_unlocked


def trash_item(user, node):
    """Move *node* and its subtree to the trash."""
    require_action("delete", user, node)
    require_unlocked(user, node)
    with refusals():
        FileService.soft_delete(node, acting_user=user)
    _notify_trashed(user, node)


def restore_item(user, node):
    """Bring *node* back from the trash, with the folders it sat in."""
    require_action("restore", user, node, in_trash=True)
    FileService.restore(node, acting_user=user)


def purge_item(user, node):
    """Delete a trashed *node* and its subtree for good."""
    require_action("purge", user, node, in_trash=True)
    FileService.hard_delete(node, acting_user=user)


def _notify_trashed(user, node):
    """Tell the people *node* was shared with that it went to the trash."""
    recipients = (
        get_user_model()
        .objects.filter(
            Q(received_shares__file=node)
            | Q(groups__received_file_shares__file=node)
            | Q(
                project_memberships__left_at__isnull=True,
                project_memberships__project__received_file_shares__file=node,
            )
            | Q(groups__projects__received_file_shares__file=node)
        )
        # Whoever trashed it knows already, even when it reached them too.
        .exclude(pk=user.pk)
        .distinct()
    )
    with transaction.atomic():
        for users in in_recipient_languages(recipients):
            notify_many(
                recipients=users,
                origin="files",
                title=gettext('%(actor)s deleted "%(name)s"')
                % {"actor": user.username, "name": node.name},
                actor=user,
            )
