"""Delete everything face grouping computed for one user.

Runs when the user turns the setting off. A face embedding is biometric data:
the rows, the vectors derived from them and the crops on storage all go, and
nothing is kept "in case they turn it back on" - that is a fresh analysis.
"""

import logging

from django.core.files.storage import default_storage
from django.db import transaction

from workspace.common.logging import scrub

from ..models import Appearance, AppearanceAnalysis, Face, FaceAnalysis, FaceCluster

logger = logging.getLogger(__name__)

_BATCH = 500


def purge_owner_faces(owner_id):
    """Delete *owner_id*'s faces, the people found by their body, clusters,
    analyses and crops; return the face count."""
    deleted = _delete_all(Face, owner_id)
    _delete_all(Appearance, owner_id)
    FaceCluster.objects.filter(owner_id=owner_id).delete()
    FaceAnalysis.objects.filter(owner_id=owner_id).delete()
    AppearanceAnalysis.objects.filter(owner_id=owner_id).delete()
    transaction.on_commit(lambda: _sweep_crops(owner_id))
    return deleted


def _delete_all(model, owner_id):
    deleted = 0
    while True:
        batch = list(
            model.objects.filter(owner_id=owner_id).values_list("pk", flat=True)[
                :_BATCH
            ]
        )
        if not batch:
            return deleted
        with transaction.atomic():
            # Instance deletes: the post_delete receivers drop each row's
            # vector and its crop (photos/signals.py).
            for row in model.objects.filter(pk__in=batch):
                row.delete()
        deleted += len(batch)


def _sweep_crops(owner_id):
    """Remove whatever is left under the owner's crop directory.

    A crop written by an analysis that crashed before its rows committed has
    no row to delete it.
    """
    directory = f"faces/{owner_id}"
    try:
        _dirs, names = default_storage.listdir(directory)
    except FileNotFoundError, NotImplementedError:
        return
    for name in names:
        try:
            default_storage.delete(f"{directory}/{name}")
        except OSError:
            logger.warning("Could not delete face crop %s/%s", directory, scrub(name))
