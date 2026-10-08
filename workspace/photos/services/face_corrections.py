"""What a user can correct in the grouping of their faces.

Every correction is recorded on the faces it touches (``assignment``,
``rejected_cluster``), which is what keeps the next clustering run from
undoing it. The caller has already checked the cluster or face is the
user's (see queries.py).

A video is the exception to the one face per photo per person rule: the
tracking may split one person in two, so a correction that would put them
twice in a video folds the newcomer into the face already them (see
fold_face) rather than being refused or displacing it.

The corrections of one face take a ``touched`` set: given one, they add the
clusters to refresh to it instead of refreshing them, so a batch refreshes
each cluster once rather than once per face.
"""

from django.db import IntegrityError, transaction
from django.db.models import Case, F, Q, Value, When
from django.utils import timezone

from ..models import Face, FaceCluster
from .face_grouping import photo_clusters, refresh_clusters


class CorrectionError(ValueError):
    """A correction that cannot apply. Each kind is its own subclass, so a
    caller answers with its own wording rather than the exception's text.

    ``conflicts`` holds the (incoming, existing) face pairs that stood in the
    way, when the refusal is one face per photo per person: what a caller
    shows the user to settle it.
    """

    def __init__(self, conflicts=()):
        super().__init__()
        self.conflicts = list(conflicts)


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


def blocking_faces(face, cluster):
    """The other faces of *face*'s photo that keep it out of *cluster*: one
    in that cluster, or in another cluster of the same person."""
    same_person = Q(cluster=cluster)
    if cluster.person_id is not None:
        same_person |= Q(cluster__person_id=cluster.person_id)
    return Face.objects.filter(same_person, file_id=face.file_id).exclude(pk=face.pk)


def _set_aside():
    """The assignment of a face a merge takes out of its cluster because its
    photo already holds that person: back to ungrouped in a photo, folded in
    a video."""
    return Case(
        When(timestamp__isnull=False, then=Value(Face.Assignment.DUPLICATE)),
        default=Value(Face.Assignment.AUTO),
    )


def _refresh(cluster_ids, touched):
    cluster_ids = set(cluster_ids) - {None}
    if touched is None:
        refresh_clusters(cluster_ids)
    else:
        touched.update(cluster_ids)


def _cover_keeper(clusters, person_id):
    """The cluster whose cover the merged one shows: among those already
    carrying the kept name, the one whose cover the user picked last, else
    the first (the target when it qualifies)."""
    named = [c for c in clusters if c.person_id == person_id] or clusters
    chosen = [c for c in named if c.cover_chosen_at is not None]
    return max(chosen, key=lambda c: c.cover_chosen_at) if chosen else named[0]


def merge_clusters(target, sources, *, person_id=None):
    """Move every face of *sources* into *target*, then delete *sources*.

    A photo already in *target*, or in another cluster of the person the
    merged cluster ends up named after, keeps its face there; the source's
    face of that photo goes back to ungrouped rather than breaking the one
    face per photo per person rule - in a video, it is folded into the face
    kept (see fold_face). So does a face of *target* itself, when
    the merge gives it a person already in that photo.

    The merged cluster shows the cover of the cluster that carried the kept
    name, so merging a stranger into someone never swaps their face; that
    cover's face wins its photo over the target's.

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
    keeper = _cover_keeper([target, *sources], person_id)
    cover_id = keeper.cover_id
    merged = {target.pk, *(cluster.pk for cluster in sources)}
    with transaction.atomic():
        same_person = (
            FaceCluster.objects.filter(person_id=person_id).exclude(pk__in=merged)
            if person_id is not None
            else FaceCluster.objects.none()
        )
        same_person_files = set(
            Face.objects.filter(cluster__in=same_person).values_list(
                "file_id", flat=True
            )
        )
        if target.person_id != person_id:
            # The target takes a name it did not have: its faces of a photo
            # that person is already in go back to ungrouped, as the
            # sources' do below.
            Face.objects.filter(cluster=target, file_id__in=same_person_files).update(
                cluster=None, assignment=_set_aside()
            )
        if keeper is not target and cover_id is not None:
            Face.objects.filter(
                cluster=target,
                file_id__in=Face.objects.filter(pk=cover_id).values("file_id"),
            ).update(cluster=None, assignment=_set_aside())
        taken = same_person_files | set(
            Face.objects.filter(cluster=target).values_list("file_id", flat=True)
        )
        faces = list(
            Face.objects.filter(cluster__in=sources)
            .order_by("-assignment", "-quality")
            .select_for_update()
        )
        # The cover first, so no other face of its photo takes its place.
        faces.sort(key=lambda face: face.pk != cover_id)
        for face in faces:
            if face.file_id in taken:
                face.cluster = None
                face.assignment = (
                    Face.Assignment.DUPLICATE
                    if face.in_video()
                    else Face.Assignment.AUTO
                )
            else:
                face.cluster = target
                taken.add(face.file_id)
            face.save(update_fields=["cluster", "assignment"])
        FaceCluster.objects.filter(pk__in=[cluster.pk for cluster in sources]).delete()
        changed = []
        if target.person_id != person_id:
            target.person_id = person_id
            changed.append("person")
        if keeper is not target:
            target.cover_id = cover_id
            target.cover_chosen_at = keeper.cover_chosen_at
            changed += ["cover", "cover_chosen_at"]
        if changed:
            target.save(update_fields=[*changed, "updated_at"])
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


def reject_face(face, *, touched=None):
    """Not this person: take *face* out of its cluster, for good."""
    previous = face.cluster_id
    if previous is None:
        return
    face.cluster = None
    face.assignment = Face.Assignment.REJECTED
    face.rejected_cluster_id = previous
    face.save(update_fields=["cluster", "assignment", "rejected_cluster"])
    _refresh([previous], touched)


def fold_face(face, *, touched=None):
    """Another face of *face*'s video is already this person: the tracking
    split one person in two. *face* leaves its cluster and the faces waiting
    for a person; the other one stands for them in the video."""
    previous = face.cluster_id
    face.cluster = None
    face.assignment = Face.Assignment.DUPLICATE
    face.save(update_fields=["cluster", "assignment"])
    _refresh([previous], touched)


def hide_face(face, *, touched=None):
    """Nobody to name: *face* leaves its cluster and grouping until unhidden.

    The cluster it leaves is remembered as rejected, so unhiding it never
    puts it straight back where the user took it from.
    """
    previous = face.cluster_id
    face.cluster = None
    face.assignment = Face.Assignment.HIDDEN
    if previous is not None:
        face.rejected_cluster_id = previous
    face.save(update_fields=["cluster", "assignment", "rejected_cluster"])
    _refresh([previous], touched)


def unhide_face(face):
    """Back among the faces waiting for a person; grouping may place it again."""
    if face.assignment != Face.Assignment.HIDDEN:
        return
    face.assignment = Face.Assignment.AUTO
    face.save(update_fields=["assignment"])


def review_cluster(cluster, *, confirmed=(), rejected=()):
    """Confirm the *confirmed* faces in *cluster*, reject the *rejected* ones.

    Only faces still in *cluster* move: one a clustering run or another
    correction took elsewhere since the user looked is left where it is.
    """
    faces = Face.objects.filter(cluster=cluster)
    with transaction.atomic():
        faces.filter(pk__in=confirmed).update(assignment=Face.Assignment.CONFIRMED)
        # The SET clauses read the row as it was: rejected_cluster takes the
        # cluster the face is leaving.
        left = faces.filter(pk__in=rejected).update(
            rejected_cluster=F("cluster"),
            cluster=None,
            assignment=Face.Assignment.REJECTED,
        )
    if left:
        refresh_clusters([cluster.pk])


@transaction.atomic
def confirm_face(face, cluster, *, replace=False, touched=None):
    """This is X: pin *face* in *cluster*, wherever it was.

    With *replace*, the face of the photo already X is not X after all: it
    is taken out of its cluster for *face* to take its place - or stays in
    it, when *face* cannot take that place after all.

    In a video already showing X, *face* is folded into the face that is,
    *replace* or not.
    """
    previous = face.cluster_id
    if face.in_video() and blocking_faces(face, cluster).exists():
        fold_face(face, touched=touched)
        return
    if replace:
        for other in blocking_faces(face, cluster):
            reject_face(other, touched=touched)
    if cluster.pk in photo_clusters(face.file_id, exclude_face=face.pk):
        raise PhotoAlreadyInCluster(
            (face, other) for other in blocking_faces(face, cluster)
        )
    face.cluster = cluster
    face.assignment = Face.Assignment.CONFIRMED
    if face.rejected_cluster_id == cluster.pk:
        face.rejected_cluster = None
    try:
        with transaction.atomic():
            face.save(update_fields=["cluster", "assignment", "rejected_cluster"])
    except IntegrityError as exc:
        raise PhotoAlreadyInCluster() from exc
    _refresh({cluster.pk, previous}, touched)


def start_cluster(face, person=None, *, touched=None):
    """This is someone new, or *person* in a new look: a cluster of its own
    for *face*, confirmed."""
    with transaction.atomic():
        cluster = FaceCluster.objects.create(owner_id=face.owner_id, person=person)
        confirm_face(face, cluster, touched=touched)
    return cluster


def set_cover(cluster, face):
    if face.cluster_id != cluster.pk:
        raise CoverNotInCluster
    cluster.cover = face
    cluster.cover_chosen_at = timezone.now()
    cluster.save(update_fields=["cover", "cover_chosen_at", "updated_at"])
