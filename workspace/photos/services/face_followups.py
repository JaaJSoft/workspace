"""What a correction of the user's sets off in the background once saved.

- The guessed faces that came into a cluster with a face the user took out
  of it leave with it (face_grouping.release_followers).
- Who the people found by their body are is suggested again: around the
  photos of the faces corrected, or across the library when a whole cluster
  changed name (appearance_suggestions).
"""

from django.core.cache import cache
from django.db import transaction

from ..models import FaceCluster

# Naming groups one after the other costs one pass over the library, this
# many seconds after the first.
_LIBRARY_DELAY = 30
_LIBRARY_KEY = "photos:appearances:suggest-queued:{}"
_LIBRARY_TTL = 10 * 60


def faces_left(owner_id, cluster_id, face_ids, destination=None):
    """The user took *face_ids* out of *cluster_id*: they are not that
    person, or are *destination*'s (a cluster). Moving a face between two
    clusters of one person corrects nobody."""
    if cluster_id is None or not _another_person(cluster_id, destination):
        faces_settled(owner_id, face_ids)
        return
    from ..tasks import release_face_followers

    args = (owner_id, str(cluster_id), [str(pk) for pk in face_ids])
    transaction.on_commit(lambda: release_face_followers.delay(*args))


def _another_person(cluster_id, destination):
    if destination is None:
        return True
    if destination.pk == cluster_id:
        return False
    return (
        destination.person_id is None
        or not FaceCluster.objects.filter(
            pk=cluster_id, person_id=destination.person_id
        ).exists()
    )


def faces_settled(owner_id, face_ids):
    """The user said who *face_ids* are, or that they are nobody to name."""
    from ..tasks import suggest_appearances_around

    args = (owner_id, [str(pk) for pk in face_ids])
    transaction.on_commit(lambda: suggest_appearances_around.delay(*args))


def names_changed(owner_id):
    """A whole cluster changed name, or showing: once per _LIBRARY_DELAY."""
    from ..tasks import suggest_appearances

    def queue():
        if cache.add(_LIBRARY_KEY.format(owner_id), True, _LIBRARY_TTL):
            suggest_appearances.apply_async((owner_id,), countdown=_LIBRARY_DELAY)

    transaction.on_commit(queue)


def library_pass_started(owner_id):
    """The next names_changed queues a pass of its own."""
    cache.delete(_LIBRARY_KEY.format(owner_id))
