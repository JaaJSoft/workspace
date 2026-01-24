import os
from django.db import models, transaction
from django.db.models import Value
from django.db.models.functions import Concat, Substr
from django.contrib.auth import get_user_model

from workspace.common.uuids import uuid_v7_or_v4

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
    content = models.FileField(upload_to=file_upload_path, null=True, blank=True)
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

    class Meta:
        ordering = ['node_type', 'name']
        indexes = [
            models.Index(fields=['parent', 'node_type']),
            models.Index(fields=['owner', 'created_at']),
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
