"""Celery tasks for face detection and grouping."""

import logging

from celery import shared_task
from django.db.models import F, Q

from workspace.common.task_priority import (
    BACKGROUND_PRIORITY,
    LOW_PRIORITY,
    NORMAL_PRIORITY,
)

logger = logging.getLogger(__name__)


@shared_task(
    name="photos.analyze_faces",
    priority=NORMAL_PRIORITY,
    bind=True,
    max_retries=0,
    ignore_result=True,
)
def analyze_photo_faces(self, file_uuid):
    """Find the faces of one photo, unless they are already up to date.

    max_retries=0: a photo that failed is still pending, and the hourly
    catch-up (or ``manage.py catch_up faces``) comes back for it.
    """
    from workspace.common.uuids import parse_uuid_or_none

    from .services.face_analysis import analyze_faces, pending_faces_qs

    file_obj = (
        pending_faces_qs()
        .select_related("owner")
        .filter(uuid=parse_uuid_or_none(file_uuid))
        .first()
    )
    if file_obj is not None:
        analyze_faces(file_obj)


@shared_task(name="photos.queue_owner_faces", priority=LOW_PRIORITY, ignore_result=True)
def queue_owner_faces(user_id):
    """Queue every photo of a user who just turned face grouping on."""
    from .services.face_analysis import faces_catch_up_enabled, pending_faces_qs

    if not faces_catch_up_enabled():
        return 0
    # Read in full before sending: with tasks run inline (development), a
    # cursor held open across their writes makes SQLite report a locked
    # database.
    uuids = list(
        pending_faces_qs().filter(owner_id=user_id).values_list("uuid", flat=True)
    )
    for uuid in uuids:
        analyze_photo_faces.apply_async((str(uuid),), priority=LOW_PRIORITY)
    return len(uuids)


@shared_task(name="photos.cluster_faces", priority=LOW_PRIORITY, ignore_result=True)
def cluster_faces(user_id):
    from .services.face_grouping import cluster_owner

    return cluster_owner(user_id)


@shared_task(name="photos.cluster_all_faces", priority=BACKGROUND_PRIORITY)
def cluster_all_faces():
    """The nightly pass: tidy up, then queue a clustering run per user.

    Also the safety net of the purge and of moves: faces of a user who is no
    longer opted in, and faces of a photo that left its owner's library,
    are deleted here if the task that should have done it was lost.
    """
    from workspace.files.models import File

    from .models import Face
    from .services.face_analysis import forget_faces
    from .services.face_preferences import faces_available, opted_in_owner_ids
    from .services.face_purge import purge_owner_faces

    opted_in = opted_in_owner_ids()
    for owner_id in (
        Face.objects.exclude(owner_id__in=opted_in)
        .values_list("owner_id", flat=True)
        .distinct()
    ):
        purge_owner_faces(owner_id)
    for file_obj in File.objects.filter(
        Q(group__isnull=False) | ~Q(owner_id=F("faces__owner_id")),
        faces__isnull=False,
    ).distinct():
        forget_faces(file_obj)
    if not faces_available():
        return 0
    owners = list(
        Face.objects.filter(owner_id__in=opted_in)
        .values_list("owner_id", flat=True)
        .distinct()
    )
    for owner_id in owners:
        cluster_faces.delay(owner_id)
    return len(owners)


@shared_task(name="photos.purge_faces", priority=NORMAL_PRIORITY, ignore_result=True)
def purge_faces(user_id):
    """Delete everything face grouping computed for a user who turned it off."""
    from workspace.users.models import UserSetting

    from .services.face_preferences import FACES_ENABLED, MODULE
    from .services.face_purge import purge_owner_faces

    # Turned back on before this ran: the data is wanted again.
    if UserSetting.objects.filter(
        user_id=user_id, module=MODULE, key=FACES_ENABLED, value=True
    ).exists():
        return 0
    return purge_owner_faces(user_id)
