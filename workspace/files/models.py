import os
from django.db import models
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

    def get_path(self):
        """Return the full path from root to this node."""
        path = [self.name]
        node = self.parent
        while node:
            path.insert(0, node.name)
            node = node.parent
        return '/'.join(path)

    def is_folder(self):
        return self.node_type == self.NodeType.FOLDER

    def is_file(self):
        return self.node_type == self.NodeType.FILE
