"""What a user can correct in the grouping of their faces.

Every correction is recorded on the faces it touches (``assignment``,
``rejected_cluster``), which is what keeps the next clustering run from
undoing it. The caller has already checked the cluster or face is the
user's (see queries.py).
"""

from django.db import IntegrityError, transaction

from ..models import Face, FaceCluster
from .face_grouping import refresh_clusters


class CorrectionError(ValueError):
    """A correction that cannot apply, with a message the user can read."""


def merge_clusters(target, sources):
    """Move every face of *sources* into *target*, then delete *sources*.

    A photo already in *target* keeps its face there; the source's face of
    that photo goes back to ungrouped rather than breaking the one face per
    photo per cluster rule.
    """
    sources = [cluster for cluster in sources if cluster.pk != target.pk]
    with transaction.atomic():
        taken = set(
            Face.objects.filter(cluster=target).values_list("file_id", flat=True)
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
    if (
        Face.objects.filter(file_id=face.file_id, cluster=cluster)
        .exclude(pk=face.pk)
        .exists()
    ):
        raise CorrectionError("Another face of this photo is already in that group.")
    face.cluster = cluster
    face.assignment = Face.Assignment.CONFIRMED
    if face.rejected_cluster_id == cluster.pk:
        face.rejected_cluster = None
    try:
        with transaction.atomic():
            face.save(update_fields=["cluster", "assignment", "rejected_cluster"])
    except IntegrityError as exc:
        raise CorrectionError(
            "Another face of this photo is already in that group."
        ) from exc
    refresh_clusters({cluster.pk, previous} - {None})


def start_cluster(face):
    """This is someone new: a cluster of its own for *face*, confirmed."""
    with transaction.atomic():
        cluster = FaceCluster.objects.create(owner_id=face.owner_id)
        confirm_face(face, cluster)
    return cluster


def set_cover(cluster, face):
    if face.cluster_id != cluster.pk:
        raise CorrectionError("The cover must be one of the group's faces.")
    cluster.cover = face
    cluster.save(update_fields=["cover", "updated_at"])
