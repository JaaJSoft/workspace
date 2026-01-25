import os
from django.db import models, transaction
from django.db.models import Value
from django.db.models.functions import Concat, Substr
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.utils import timezone

from workspace.common.uuids import uuid_v7_or_v4
from .storage import OverwriteStorage

User = get_user_model()


def file_upload_path(instance, filename):
    """Generate upload path based on the node's position in the tree."""
    # Get the directory path from parent nodes
    path_parts = []
    node = instance.parent
    while node:
        path_parts.insert(0, node.name)
        node = node.parent

    # Add owner's username to isolate files by user
    path_parts.insert(0, f'{instance.owner.username}')

    # Combine with filename
    if path_parts:
        return os.path.join('files', *path_parts, filename)
    return os.path.join('files', filename)


class File(models.Model):
    """Model representing a file or folder in a tree structure."""

    class NodeType(models.TextChoices):
        FILE = 'file', 'File'
        FOLDER = 'folder', 'Folder'

    uuid = models.UUIDField(primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4)
    name = models.CharField(max_length=255)
    node_type = models.CharField(max_length=10, choices=NodeType.choices)
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )

    # File-specific fields
    content = models.FileField(
        upload_to=file_upload_path,
        storage=OverwriteStorage(),
        null=True,
        blank=True,
        max_length=1024
    )
    size = models.BigIntegerField(null=True, blank=True, help_text="File size in bytes")
    mime_type = models.CharField(max_length=100, null=True, blank=True)
    path = models.TextField(
        blank=True,
        editable=False,
        help_text="Full path from root to this node."
    )

    # Metadata
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='files')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['node_type', 'name']
        indexes = [
            models.Index(fields=['parent', 'node_type']),
            models.Index(fields=['owner', 'created_at']),
            models.Index(fields=['owner', 'deleted_at'], name='file_owner_del_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(node_type='folder') &
                        (models.Q(content__isnull=True) | models.Q(content=''))
                    ) |
                    models.Q(node_type='file')
                ),
                name='folder_has_no_content'
            ),
        ]

    def __str__(self):
        return f"{self.get_node_type_display()}: {self.name}"

    @classmethod
    def _update_descendant_paths(cls, old_path, new_path):
        prefix = f"{old_path}/"
        start_pos = len(old_path) + 2
        cls.objects.filter(path__startswith=prefix).update(
            path=Concat(Value(f"{new_path}/"), Substr('path', start_pos))
        )

    @classmethod
    def _build_path_for(cls, name, parent_id):
        if parent_id:
            parent = cls.objects.only('path', 'name', 'parent_id').get(pk=parent_id)
            parent_path = parent.path or parent.get_path()
            return f"{parent_path}/{name}"
        return name

    def save(self, *args, **kwargs):
        old_data = None
        if self.pk:
            old_data = File.objects.filter(pk=self.pk).values(
                'name', 'parent_id', 'path'
            ).first()

        new_path = self._build_path_for(self.name, self.parent_id)
        self.path = new_path

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'path'}

        if old_data:
            old_path = old_data.get('path')
            if not old_path:
                old_path = self._build_path_for(
                    old_data['name'], old_data['parent_id']
                )
            if old_path and old_path != new_path:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                    self._update_descendant_paths(old_path, new_path)
                return

        super().save(*args, **kwargs)

    def get_path(self):
        """Return the full path from root to this node."""
        if self.path:
            return self.path
        if self.parent_id:
            return f"{self.parent.get_path()}/{self.name}"
        return self.name

    def is_folder(self):
        return self.node_type == self.NodeType.FOLDER

    def is_file(self):
        return self.node_type == self.NodeType.FILE

    def is_viewable(self):
        """Check if this file can be viewed in the browser."""
        from .utils import FileTypeDetector
        if self.node_type != self.NodeType.FILE:
            return False
        return FileTypeDetector.is_viewable(self.mime_type or '')

    def is_deleted(self):
        return self.deleted_at is not None

    def _descendant_filter(self):
        if self.node_type != self.NodeType.FOLDER:
            return models.Q(pk=self.pk)
        path = self.path or self.get_path()
        if not path:
            return models.Q(pk=self.pk)
        prefix = f"{path}/"
        return models.Q(pk=self.pk) | models.Q(path__startswith=prefix)

    def soft_delete(self, deleted_at=None):
        if deleted_at is None:
            deleted_at = timezone.now()
        return File.objects.filter(
            self._descendant_filter(),
            deleted_at__isnull=True,
        ).update(deleted_at=deleted_at)

    def _restore_parents(self):
        parent_id = self.parent_id
        restored_ids = []
        while parent_id:
            parent = File.objects.filter(pk=parent_id).values('pk', 'parent_id', 'deleted_at').first()
            if not parent or parent['deleted_at'] is None:
                break
            restored_ids.append(parent['pk'])
            parent_id = parent['parent_id']
        if restored_ids:
            File.objects.filter(pk__in=restored_ids).update(deleted_at=None)
        return len(restored_ids)

    def restore(self):
        if self.node_type == self.NodeType.FOLDER:
            updated = File.objects.filter(self._descendant_filter()).update(deleted_at=None)
        else:
            if self.deleted_at is None:
                updated = 0
            else:
                self.deleted_at = None
                self.save(update_fields=['deleted_at'])
                updated = 1
        self._restore_parents()
        return updated

    def delete(self, *args, **kwargs):
        """Soft-delete by default; pass hard=True to permanently delete."""
        hard = kwargs.pop('hard', False)
        if hard:
            return super().delete(*args, **kwargs)
        return self.soft_delete()


class FileFavorite(models.Model):
    """User favorites for files or folders."""
    uuid = models.UUIDField(primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='file_favorites',
    )
    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name='favorites',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'file'],
                name='unique_file_favorite',
            ),
        ]
        indexes = [
            models.Index(fields=['owner', 'created_at'], name='file_fav_owner_created'),
        ]

    def __str__(self):
        return f"{self.owner} -> {self.file}"


# Signal to handle file deletion when using QuerySet.delete() or bulk operations
@receiver(pre_delete, sender=File)
def delete_file_on_delete(sender, instance, **kwargs):
    """
    Delete the physical file or folder when a File instance is deleted.
    This signal ensures files are deleted even in bulk operations.
    For folders, attempts to remove the physical directory if it exists.
    """
    from django.core.files.storage import default_storage
    import logging
    import os
    import shutil

    logger = logging.getLogger(__name__)

    if instance.node_type == File.NodeType.FILE and instance.content:
        # Handle file deletion
        try:
            file_path = instance.content.name
            if file_path and default_storage.exists(file_path):
                default_storage.delete(file_path)
                logger.info(f"Signal: Deleted physical file: {file_path}")

                # Try to remove empty parent directories
                try:
                    dir_path = os.path.dirname(file_path)
                    while dir_path and dir_path != 'files':
                        full_path = os.path.join(default_storage.location, dir_path)
                        if os.path.exists(full_path) and os.path.isdir(full_path):
                            if not os.listdir(full_path):  # Directory is empty
                                os.rmdir(full_path)
                                logger.info(f"Signal: Deleted empty directory: {dir_path}")
                                dir_path = os.path.dirname(dir_path)
                            else:
                                break  # Directory not empty, stop
                        else:
                            break
                except Exception as e:
                    logger.warning(f"Signal: Could not remove empty directory for {file_path}: {e}")

        except Exception as e:
            logger.error(f"Signal: Error deleting physical file {instance.content.name}: {e}")

    elif instance.node_type == File.NodeType.FOLDER:
        # Handle folder deletion - remove the physical directory if it exists
        try:
            # Build the folder path
            folder_path = instance.path or instance.get_path()
            if folder_path:
                # Convert path to file system path
                full_path = os.path.join(default_storage.location, 'files', instance.owner.username, folder_path.split('/', 1)[1] if '/' in folder_path else folder_path)

                if os.path.exists(full_path) and os.path.isdir(full_path):
                    # Remove directory and all its contents (in case there are orphaned files)
                    shutil.rmtree(full_path)
                    logger.info(f"Signal: Deleted folder and contents: {full_path}")
        except Exception as e:
            logger.warning(f"Signal: Could not delete folder {instance.name}: {e}")
