"""What a user can correct in the grouping of their faces.

Every correction is recorded on the faces it touches (``assignment``,
``rejected_cluster``), which is what keeps the next clustering run from
undoing it. The caller has already checked the cluster or face is the
user's (see queries.py).
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Face, FaceCluster
from .face_grouping import photo_clusters, refresh_clusters


class CorrectionError(ValueError):
    """A correction that cannot apply. Each kind is its own subclass, so a
    caller answers with its own wording rather than the exception's text."""


class PhotoAlreadyInCluster(CorrectionError):
    """Another face of the same photo is already in the target cluster."""


class CoverNotInCluster(CorrectionError):
    """The face chosen as cover is not one of the cluster's."""


class PersonsDiffer(CorrectionError):
    """The clusters to merge are named after different people, and the
    caller did not say which name the merged cluster keeps."""

    def __init__(self, person_ids):
        super().__init__()
        self.person_ids = sorted(person_ids, key=str)


def merge_clusters(target, sources, *, person_id=None):
    """Move every face of *sources* into *target*, then delete *sources*.

    A photo already in *target*, or in another cluster of the person the
    merged cluster ends up named after, keeps its face there; the source's
    face of that photo goes back to ungrouped rather than breaking the one
    face per photo per person rule.

    When the clusters are named after different people, *person_id* says
    which one the merged cluster keeps; without it the merge is refused.
    """
    sources = [cluster for cluster in sources if cluster.pk != target.pk]
    named = {c.person_id for c in (target, *sources) if c.person_id is not None}
    if person_id is None and len(named) > 1:
        raise PersonsDiffer(named)
    if person_id is not None and person_id not in named:
        raise PersonsDiffer(named)
    person_id = person_id if person_id is not None else next(iter(named), None)
    merged = {target.pk, *(cluster.pk for cluster in sources)}
    with transaction.atomic():
        same_person = (
            FaceCluster.objects.filter(person_id=person_id).exclude(pk__in=merged)
            if person_id is not None
            else FaceCluster.objects.none()
        )
        taken = set(
            Face.objects.filter(cluster__in=[target.pk, *same_person]).values_list(
                "file_id", flat=True
            )
        )
        for face in (
            Face.objects.filter(cluster__in=sources)
            .order_by("-assignment", "-quality")
            .select_for_update()
        ):
            if face.file_id in taken:
                face.cluster = None
                face.assignment = Face.Assignment.AUTO
            else:
                face.cluster = target
                taken.add(face.file_id)
            face.save(update_fields=["cluster", "assignment"])
        FaceCluster.objects.filter(pk__in=[cluster.pk for cluster in sources]).delete()
        if target.person_id != person_id:
            target.person_id = person_id
            target.save(update_fields=["person", "updated_at"])
    refresh_clusters([target.pk])


def set_hidden(cluster, hidden):
    cluster.hidden = hidden
    cluster.save(update_fields=["hidden", "updated_at"])


def ungroup(cluster):
    """Delete *cluster*; its faces leave automatic grouping for good.

    Without that, the next clustering run would rebuild the same cluster
    from the same faces.
    """
    with transaction.atomic():
        Face.objects.filter(cluster=cluster).update(
            cluster=None, assignment=Face.Assignment.REJECTED, rejected_cluster=None
        )
        cluster.delete()


def reject_face(face):
    """Not this person: take *face* out of its cluster, for good."""
    previous = face.cluster_id
    if previous is None:
        return
    face.cluster = None
    face.assignment = Face.Assignment.REJECTED
    face.rejected_cluster_id = previous
    face.save(update_fields=["cluster", "assignment", "rejected_cluster"])
    refresh_clusters([previous])


def confirm_face(face, cluster):
    """This is X: pin *face* in *cluster*, wherever it was."""
    previous = face.cluster_id
    if cluster.pk in photo_clusters(face.file_id, exclude_face=face.pk):
        raise PhotoAlreadyInCluster
    face.cluster = cluster
    face.assignment = Face.Assignment.CONFIRMED
    if face.rejected_cluster_id == cluster.pk:
        face.rejected_cluster = None
    try:
        with transaction.atomic():
            face.save(update_fields=["cluster", "assignment", "rejected_cluster"])
    except IntegrityError as exc:
        raise PhotoAlreadyInCluster from exc
    refresh_clusters({cluster.pk, previous} - {None})


def start_cluster(face, person=None):
    """This is someone new, or *person* in a new look: a cluster of its own
    for *face*, confirmed."""
    with transaction.atomic():
        cluster = FaceCluster.objects.create(owner_id=face.owner_id, person=person)
        confirm_face(face, cluster)
    return cluster


def set_cover(cluster, face):
    if face.cluster_id != cluster.pk:
        raise CoverNotInCluster
    cluster.cover = face
    cluster.cover_chosen_at = timezone.now()
    cluster.save(update_fields=["cover", "cover_chosen_at", "updated_at"])
