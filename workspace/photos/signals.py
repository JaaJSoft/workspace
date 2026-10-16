"""Keep face data and photo embeddings in step with the rows and settings they
derive from, and album sharing in step with the memberships it rests on.

Connected in PhotosConfig.ready().
"""

import logging

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from workspace.common.logging import scrub
from workspace.common.vectors.indexing import drop_vector
from workspace.projects.models import Project, ProjectMember
from workspace.users.models import UserSetting

from .indexes import FACE_EMBEDDINGS, PHOTO_EMBEDDINGS
from .models import Face, MediaItem, PhotoEmbedding
from .services.album_revocation import revoke_lapsed_sharing
from .services.face_preferences import FACES_ENABLED, MODULE
from .services.photo_embeddings import delete_frames

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Face)
def drop_face_derivatives(sender, instance, using, **kwargs):
    """A deleted face takes its vector and its crop with it.

    Covers every path that deletes faces, a File purged from the trash (a
    cascade) included. The crop goes once the delete has committed: a
    rolled-back delete must still find it.
    """
    drop_vector(FACE_EMBEDDINGS, instance.pk, using=using)
    if instance.crop:
        name = instance.crop
        transaction.on_commit(lambda: _delete_crop(name), using=using)


@receiver(post_delete, sender=PhotoEmbedding)
def drop_photo_embedding_derivatives(sender, instance, using, **kwargs):
    """A deleted embedding takes its vector and its key frame's picture with
    it, a File purged from the trash (a cascade) included. The picture goes
    once the delete has committed, as a face crop does."""
    drop_vector(PHOTO_EMBEDDINGS, instance.pk, using=using)
    if instance.frame:
        name = instance.frame
        transaction.on_commit(lambda: delete_frames([name]), using=using)


def _delete_crop(name):
    try:
        default_storage.delete(name)
    except OSError:
        logger.warning("Could not delete face crop %s", scrub(name))


@receiver(post_delete, sender=MediaItem)
def drop_renditions(sender, instance, using, **kwargs):
    """A file leaving the library (hard deleted, or no longer a photo) takes
    the renditions albums served of it along, once the delete commits."""
    from .services.album_renditions import drop_renditions as drop

    file_id = instance.file_id
    transaction.on_commit(lambda: drop(file_id), using=using)


@receiver(post_save, sender=UserSetting)
@receiver(post_delete, sender=UserSetting)
def follow_faces_setting(sender, instance, **kwargs):
    """Turning face grouping off purges it; turning it on queues the library.

    A receiver rather than a dedicated endpoint, so every path that writes
    the setting - the generic settings API included - honours it.
    """
    if instance.module != MODULE or instance.key != FACES_ENABLED:
        return
    enabled = kwargs.get("signal") is post_save and instance.value is True
    user_id = instance.user_id

    from .tasks import purge_faces, queue_owner_faces

    if enabled:
        transaction.on_commit(lambda: queue_owner_faces.delay(user_id))
    else:
        transaction.on_commit(lambda: purge_faces.delay(user_id))


# A ``clear()`` leaves nothing saying which memberships it removed once it
# ran, so ``pre_clear`` writes down who stands to lose one, for
# ``post_clear`` to check once the rows are gone.
_PENDING_CLEAR = "_photos_album_sharing_pending_clear"


def _members_of(group_ids):
    return list(
        get_user_model()
        .objects.filter(groups__in=group_ids)
        .values_list("pk", flat=True)
        .distinct()
    )


def _users_losing_membership(instance, action, pk_set, users_of):
    """The users who may have lost a membership in this ``m2m_changed``
    *action*, or None when it removes none. *users_of(instance, pk_set)*
    names those a removal of *pk_set* (all of them for None) reaches."""
    if action == "post_remove":
        return users_of(instance, pk_set or ())
    if action == "pre_clear":
        setattr(instance, _PENDING_CLEAR, users_of(instance, None))
        return None
    if action == "post_clear":
        return instance.__dict__.pop(_PENDING_CLEAR, None)
    return None


def _revoke_for(user_ids):
    if user_ids:
        revoke_lapsed_sharing(user_ids=set(user_ids))


def _group_membership_users(instance, pk_set):
    if not isinstance(instance, get_user_model()):
        # group.user_set: the users removed, or every member on a clear.
        if pk_set is None:
            return _members_of([instance.pk])
        return list(pk_set)
    return [instance.pk]


@receiver(
    m2m_changed,
    sender=get_user_model().groups.through,
    dispatch_uid="photos_revoke_album_sharing_on_group_leave",
)
def revoke_album_sharing_on_group_leave(sender, instance, action, pk_set, **kwargs):
    """A user out of a group may have lost the right to share a group album,
    or an album shared with the group or with a project the group is on:
    the links and shares they made there are revoked (``album_revocation``).
    """
    _revoke_for(
        _users_losing_membership(instance, action, pk_set, _group_membership_users)
    )


def _project_group_users(instance, pk_set):
    if isinstance(instance, Project):
        groups = instance.groups.values("pk") if pk_set is None else pk_set
        return _members_of(groups)
    # group.projects: the group's members lose whatever project they reached.
    return _members_of([instance.pk])


@receiver(
    m2m_changed,
    sender=Project.groups.through,
    dispatch_uid="photos_revoke_album_sharing_on_project_group_removal",
)
def revoke_album_sharing_on_project_group_removal(
    sender, instance, action, pk_set, **kwargs
):
    """A group taken off a project takes its members out of the albums
    shared with the project, unless they are in it by name."""
    _revoke_for(
        _users_losing_membership(instance, action, pk_set, _project_group_users)
    )


@receiver(
    post_save,
    sender=ProjectMember,
    dispatch_uid="photos_revoke_album_sharing_on_project_leave",
)
def revoke_album_sharing_on_project_leave(sender, instance, **kwargs):
    """A member who left a project (``left_at`` set, see
    ``projects.services.members.remove_member``) may have lost an album
    shared with it."""
    if instance.left_at is not None:
        _revoke_for([instance.user_id])
