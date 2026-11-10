"""Trashing, restoring and purging nodes.

Three levels, each built on the next. ``trash_item``, ``restore_item`` and
``purge_item`` act on a user's behalf: every path that does one of these for
a user comes here, so the action registry, the lock and the notifications
are asked once, and a refusal is a :class:`~.refusals.Refused`.
``soft_delete`` and ``restore`` are for a caller that has decided by other
means (the disk sync, a group's deletion): they keep the tree's own rules
and record the event. ``trash_subtree`` and ``restore_subtree`` move the
rows and the bytes, and nothing else.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext

from workspace.notifications.services.notifications import notify_many
from workspace.users.services.settings import in_recipient_languages

from ..models import File, FileEvent
from . import names, storage_ops
from .events import record_event
from .hard_delete import hard_delete_tree
from .refusals import refusals, require_action, require_unlocked
from .trash_paths import trash_root_of
from .tree import GroupRootFolder, is_group_root


def trash_item(user, node):
    """Move *node* and its subtree to the trash."""
    require_action("delete", user, node)
    require_unlocked(user, node)
    with refusals():
        soft_delete(node, acting_user=user)
    _notify_trashed(user, node)


def restore_item(user, node):
    """Bring *node* back from the trash, with the folders it sat in."""
    require_action("restore", user, node, in_trash=True)
    restore(node, acting_user=user)


def purge_item(user, node):
    """Delete a trashed *node* and its subtree for good."""
    require_action("purge", user, node, in_trash=True)
    hard_delete_tree(node)


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


def soft_delete(file_obj, *, acting_user=None, deleted_at=None, details=None):
    """Soft-delete a file or folder (cascades to descendants).

    *deleted_at* defaults to now; *details* go into the recorded event.
    """
    if is_group_root(file_obj):
        raise GroupRootFolder(gettext("A group's root folder cannot be deleted."))
    return _trash_and_record(file_obj, acting_user, deleted_at, details)


def trash_group_files(group):
    """Trash everything *group* holds, its root folders included.

    Only for the group's own deletion: a root folder stays as long as its
    group exists. Through the root folders rather than in one queryset
    update: trashing a node moves its bytes out of the live tree.
    """
    now = timezone.now()
    roots = File.objects.filter(
        group=group, deleted_at__isnull=True, parent__isnull=True
    ).select_related("owner")
    for root in roots:
        _trash_and_record(root, None, now, None)
    # Anything the group owns outside those roots (legacy rows re-parented
    # by hand) still has to leave the live listing.
    File.objects.filter(group=group, deleted_at__isnull=True).update(deleted_at=now)


def _trash_and_record(file_obj, acting_user, deleted_at, details):
    count = trash_subtree(file_obj, deleted_at=deleted_at)
    record_event(
        file_obj,
        acting_user,
        FileEvent.Action.DELETED,
        {"cascade_count": count, **(details or {})},
    )
    return count


def restore(file_obj, *, acting_user=None):
    """Restore a soft-deleted file or folder from trash."""
    count = restore_subtree(file_obj)
    record_event(
        file_obj,
        acting_user,
        FileEvent.Action.RESTORED,
        {
            "cascade_count": count,
        },
    )
    return count


@transaction.atomic
def trash_subtree(node, deleted_at=None):
    """Trash *node* and its subtree, moving the bytes out of the tree.

    The storage move is last: the name a trashed node used to occupy has
    to be free for the next file to claim, and leaving the bytes there
    would let that file overwrite them.
    """
    if deleted_at is None:
        deleted_at = timezone.now()

    source = storage_ops.current_node_path(node)
    count = File.objects.filter(
        node._descendant_filter(),
        deleted_at__isnull=True,
    ).update(deleted_at=deleted_at)
    if not count:
        return 0

    node.deleted_at = deleted_at
    if source:
        storage_ops.move_node_storage(node, source, storage_ops.trash_node_path(node))
    return count


@transaction.atomic
def restore_subtree(node):
    """Bring *node* back into the live tree, bytes included.

    The node that physically moves is the outermost trashed node of the
    chain - restoring a file whose folder was trashed brings the folder
    back too, which is what ``_restore_parents`` already did in the
    database.
    """
    if node.deleted_at is None and node.node_type != File.NodeType.FOLDER:
        return 0

    root = trash_root_of(node)
    if root is not None and root.pk == node.pk:
        # Same row: work through one instance, or the rename below and
        # the save() after it would fight over ``name`` and ``path``.
        root = node
    source = storage_ops.current_node_path(root) if root is not None else None
    trashed_at = node.deleted_at
    root_trashed_at = root.deleted_at if root is not None else None
    claimant = root or node

    for _ in range(names.CLAIM_ATTEMPTS):
        if root is not None and names.find_node_conflict(
            root.owner, root.parent, root.name, exclude_pk=root.pk
        ):
            # Something took the name while the node sat in the trash.
            root.name = names.available_node_name(
                root.owner, root.parent, root.name, root.node_type
            )
            root.save(update_fields=["name"])
        try:
            with names.name_claim(claimant.owner, claimant.parent, claimant.name):
                updated = _bring_back(node, trashed_at)
                _restore_parents(node)
            break
        except names.NameTaken:
            continue  # taken between the check and the write: pick again
    else:
        raise names.NameTaken()
    node.deleted_at = None

    if root is not None and source:
        root.deleted_at = None
        storage_ops.move_node_storage(root, source, storage_ops.live_node_path(root))
        storage_ops.reconcile_trashed_children(root, root_trashed_at)
    return updated


def _bring_back(node, trashed_at):
    if node.node_type != File.NodeType.FOLDER:
        return File.objects.filter(pk=node.pk).update(deleted_at=None)
    subtree = File.objects.filter(node._descendant_filter())
    if trashed_at is not None:
        # What was trashed with the folder. A child deleted on its own
        # before it stays in the trash - its name may well be taken by
        # the node created to replace it.
        subtree = subtree.filter(deleted_at=trashed_at)
    return subtree.update(deleted_at=None)


def _restore_parents(node):
    parent_id = node.parent_id
    restored_ids = []
    while parent_id:
        parent = (
            File.objects.filter(pk=parent_id)
            .values("pk", "parent_id", "deleted_at")
            .first()
        )
        if not parent or parent["deleted_at"] is None:
            break
        restored_ids.append(parent["pk"])
        parent_id = parent["parent_id"]
    if restored_ids:
        File.objects.filter(pk__in=restored_ids).update(deleted_at=None)
    return len(restored_ids)
