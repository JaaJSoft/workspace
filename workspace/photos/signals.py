"""Keep face data and photo embeddings in step with the rows and settings they
derive from.

Connected in PhotosConfig.ready().
"""

import logging

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from workspace.common.logging import scrub
from workspace.common.vectors.indexing import drop_vector
from workspace.users.models import UserSetting

from .indexes import FACE_EMBEDDINGS, PHOTO_EMBEDDINGS
from .models import Face, MediaItem, PhotoEmbedding
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
