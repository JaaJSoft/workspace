"""Permanent deletion of File rows in bounded batches.

``File`` has ``pre_delete`` receivers (storage cleanup, search unindexing), so
Django cannot fast-delete it: the collector loads every row it is asked to
delete, every descendant the ``parent`` cascade reaches, and their related
rows, before issuing a single ``DELETE``. Deleting a whole tree in one call
therefore holds the whole tree in memory.

Deleting the deepest rows first keeps each call small: by the time a folder
comes up, its children are already gone and the cascade has nothing left to
pull in. Only primary keys are held for the whole tree.
"""

from itertools import batched

from django.db.models.functions import Length

from ..models import File

PURGE_BATCH_SIZE = 500


def purge_queryset(queryset):
    """Permanently delete every row of *queryset*, deepest first."""
    # A descendant's path extends its ancestor's, so it is strictly longer.
    pks = queryset.order_by(Length("path").desc()).values_list("pk", flat=True)
    _delete_in_batches(list(pks))


def purge_node(node):
    """Permanently delete *node* and its subtree."""
    if node.node_type == File.NodeType.FOLDER:
        _delete_in_batches(_descendant_pks_deepest_first(node))
    # Through the instance, so the caller's object ends up like any deleted one.
    node.delete(hard=True)


def _descendant_pks_deepest_first(folder):
    # Walks the parent links rather than matching ``path``: two trashed folders
    # can share a path, and a prefix match would take the other one's subtree.
    levels = []
    frontier = [folder.pk]
    while frontier:
        frontier = [
            pk
            for chunk in batched(frontier, PURGE_BATCH_SIZE, strict=False)
            for pk in File.objects.filter(parent_id__in=chunk).values_list(
                "pk", flat=True
            )
        ]
        levels.append(frontier)
    return [pk for level in reversed(levels) for pk in level]


def _delete_in_batches(pks):
    for batch in batched(pks, PURGE_BATCH_SIZE, strict=False):
        # The storage cleanup receiver reads owner.username on every row.
        File.objects.filter(pk__in=batch).select_related("owner").delete()
