"""Centralized file management service.

All file/folder business logic lives here so it can be reused by the API
serializers, the sync service, future WebDAV integration, or any other module.
"""

import gc
import logging
import mimetypes
import os

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from workspace.files.models import File

logger = logging.getLogger(__name__)


class FileService:
    """Stateless service that encapsulates file/folder business logic."""

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @staticmethod
    def create_file(owner, name, parent=None, *, content=None, mime_type=None):
        """Create a new file record, optionally with uploaded content.

        Args:
            owner: User instance.
            name: Display name.
            parent: Parent File (folder) or None for root.
            content: An uploaded file object (UploadedFile / ContentFile) or None.
            mime_type: Explicit MIME type. Inferred from *content* / *name* when omitted.

        Returns:
            The saved File instance.
        """
        if not mime_type and content is not None:
            mime_type = FileService.infer_mime_type(name, uploaded=content)

        size = content.size if content is not None else None

        file_obj = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            parent=parent,
            mime_type=mime_type or 'application/octet-stream',
            size=size,
        )
        if content is not None:
            file_obj.content = content
        file_obj.save()
        return file_obj

    @staticmethod
    def create_folder(owner, name, parent=None, *, icon=None, color=None):
        """Create a new folder record.

        Also creates the directory on the storage backend when supported
        (e.g. ``FileSystemStorage``).

        Returns:
            The saved File instance (node_type=FOLDER).
        """
        folder = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FOLDER,
            parent=parent,
            icon=icon,
            color=color,
        )
        folder.save()
        FileService._ensure_folder_on_storage(folder)
        return folder

    @staticmethod
    def register_disk_file(owner, name, parent, content_path, *, mime_type=None, size=None):
        """Register a file that already exists on disk (used by sync).

        Unlike *create_file*, this does **not** re-upload the file through
        Django's storage layer.  It sets ``content.name`` directly so the
        FileField points to the existing path.

        Args:
            content_path: Storage-relative path, e.g. ``files/alice/doc.pdf``.
        """
        if not mime_type:
            mime_type = FileService.infer_mime_type(name)

        file_obj = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            parent=parent,
            mime_type=mime_type,
            size=size,
        )
        file_obj.content.name = content_path
        file_obj.save()
        return file_obj

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @staticmethod
    def rename(file_obj, new_name):
        """Rename a file or folder, moving physical storage files as needed.

        For files: renames the physical file on storage.
        For folders: recursively renames all contained files' storage paths.

        Returns:
            The updated File instance.
        """
        old_name = file_obj.name
        if old_name == new_name:
            return file_obj

        if file_obj.node_type == File.NodeType.FOLDER:
            FileService._rename_folder_storage(file_obj, old_name, new_name)
        elif file_obj.content and file_obj.content.name:
            FileService._rename_file_storage(file_obj, new_name)

        file_obj.name = new_name
        file_obj.save()
        return file_obj

    @staticmethod
    def update_content(file_obj, content, *, name=None):
        """Replace a file's content, updating size and MIME type.

        Args:
            content: An uploaded file object.
            name: Optional new name (used for MIME inference).

        Returns:
            The updated File instance.
        """
        effective_name = name or file_obj.name
        file_obj.size = content.size
        file_obj.mime_type = FileService.infer_mime_type(effective_name, uploaded=content)
        file_obj.content = content
        file_obj.save()
        return file_obj

    @staticmethod
    def copy(file_obj, target_parent, owner):
        """Recursively copy a file or folder to *target_parent*.

        Handles name conflicts by appending "(Copy)", "(Copy 2)", etc.

        Returns:
            The root of the copied tree.
        """
        return FileService._copy_node(file_obj, target_parent, owner)

    # ------------------------------------------------------------------
    # Validation (raise ValueError on failure)
    # ------------------------------------------------------------------

    @staticmethod
    def check_name_available(owner, parent, name, node_type, *, exclude_pk=None):
        """Raise ``ValueError`` if a file with the same name already exists.

        Only enforced for *files* (not folders), case-insensitive, ignoring
        soft-deleted records.
        """
        if node_type != File.NodeType.FILE:
            return

        qs = File.objects.filter(
            owner=owner,
            parent=parent,
            node_type=File.NodeType.FILE,
            name__iexact=name,
            deleted_at__isnull=True,
        )
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if qs.exists():
            raise ValueError('A file with the same name already exists in this folder.')

    @staticmethod
    def validate_move_target(file_obj, new_parent):
        """Raise ``ValueError`` if *new_parent* is an invalid move target.

        Checks:
        - Cannot move to a folder owned by another user.
        - Cannot move a folder into itself.
        - Cannot move a folder into one of its descendants.
        """
        if new_parent is None:
            return

        if new_parent.owner_id != file_obj.owner_id:
            raise ValueError('Cannot move to a folder owned by another user.')

        if file_obj.node_type == File.NodeType.FOLDER:
            if new_parent.pk == file_obj.pk:
                raise ValueError('Cannot move a folder into itself.')

            file_path = file_obj.path or file_obj.get_path()
            parent_path = new_parent.path or new_parent.get_path()
            if parent_path.startswith(f"{file_path}/"):
                raise ValueError('Cannot move a folder into one of its descendants.')

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def infer_mime_type(filename, *, uploaded=None):
        """Infer MIME type with priority: upload metadata > filename > default.

        Args:
            filename: The file's display name.
            uploaded: Optional uploaded file object with a ``content_type`` attr.

        Returns:
            A MIME type string, never None.
        """
        if uploaded is not None:
            content_type = getattr(uploaded, 'content_type', None)
            if content_type and content_type != 'application/octet-stream':
                return content_type

        candidate = filename or (getattr(uploaded, 'name', None) if uploaded else None)
        if candidate:
            guessed, _ = mimetypes.guess_type(candidate)
            if guessed:
                return guessed

        if uploaded is not None:
            return getattr(uploaded, 'content_type', None) or 'application/octet-stream'
        return 'application/octet-stream'

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rename_file_storage(file_obj, new_name):
        """Rename a single file on disk."""
        old_path = file_obj.content.name
        dir_path = os.path.dirname(old_path)

        _, ext = os.path.splitext(old_path)
        if '.' not in new_name and ext:
            new_filename = f"{new_name}{ext}"
        else:
            new_filename = new_name
        new_path = os.path.join(dir_path, new_filename)

        if not default_storage.exists(old_path):
            logger.warning("Old file does not exist: '%s'", old_path)
            return

        file_handle = None
        try:
            file_handle = default_storage.open(old_path, 'rb')
            content = file_handle.read()
        finally:
            if file_handle:
                file_handle.close()
                gc.collect()  # release handles on Windows

        saved_path = default_storage.save(new_path, ContentFile(content))
        file_obj.content.name = saved_path

        if old_path != saved_path:
            try:
                default_storage.delete(old_path)
            except Exception as e:
                logger.warning("Could not delete old file '%s': %s", old_path, e)

    @staticmethod
    def _rename_folder_storage(folder, old_folder_name, new_folder_name):
        """Recursively rename storage paths for all files inside a folder."""
        children = File.objects.filter(parent=folder)

        for child in children:
            if child.node_type == File.NodeType.FILE and child.content and child.content.name:
                try:
                    old_path = child.content.name
                    segments = old_path.split('/')
                    for i, seg in enumerate(segments):
                        if seg == old_folder_name:
                            segments[i] = new_folder_name
                            break
                    new_path = '/'.join(segments)

                    if old_path != new_path and default_storage.exists(old_path):
                        file_handle = None
                        try:
                            file_handle = default_storage.open(old_path, 'rb')
                            content = file_handle.read()
                        finally:
                            if file_handle:
                                file_handle.close()
                                gc.collect()

                        saved_path = default_storage.save(new_path, ContentFile(content))
                        child.content.name = saved_path
                        child.save(update_fields=['content'])

                        try:
                            default_storage.delete(old_path)
                        except Exception as e:
                            logger.warning("Could not delete old file '%s': %s", old_path, e)
                except Exception as e:
                    logger.error("Error moving file '%s': %s", child.name, e)

            elif child.node_type == File.NodeType.FOLDER:
                FileService._rename_folder_storage(child, old_folder_name, new_folder_name)

    @staticmethod
    def _ensure_folder_on_storage(folder):
        """Create the folder's directory on the storage backend if supported.

        Builds the same path hierarchy used by ``file_upload_path``
        (``files/<username>/<parent…>/<name>``) and calls ``os.makedirs``
        when the storage exposes a local filesystem path.  Silently ignored
        for backends that don't (e.g. S3).
        """
        parts = [folder.name]
        node = folder.parent
        while node:
            parts.insert(0, node.name)
            node = node.parent
        parts.insert(0, folder.owner.username)
        storage_path = os.path.join('files', *parts)
        try:
            full_path = default_storage.path(storage_path)
            os.makedirs(full_path, exist_ok=True)
        except NotImplementedError:
            pass

    @staticmethod
    def _copy_node(node, parent, owner):
        """Recursively copy a single node."""
        new_name = FileService._unique_copy_name(node.name, node.node_type, parent, owner)

        copied = File(
            owner=owner,
            name=new_name,
            node_type=node.node_type,
            parent=parent,
            mime_type=node.mime_type,
            icon=node.icon,
            color=node.color,
        )

        if node.node_type == File.NodeType.FILE and node.content:
            try:
                node.content.open('rb')
                data = node.content.read()
            finally:
                node.content.close()
            copied.content = ContentFile(data, name=new_name)
            copied.size = node.size

        copied.save()

        if node.node_type == File.NodeType.FOLDER:
            for child in File.objects.filter(parent=node, deleted_at__isnull=True):
                FileService._copy_node(child, copied, owner)

        return copied

    @staticmethod
    def _unique_copy_name(base_name, node_type, parent, owner):
        """Generate a unique "(Copy)" name avoiding conflicts."""
        existing_names = set(
            File.objects.filter(
                owner=owner,
                parent=parent,
                deleted_at__isnull=True,
            ).values_list('name', flat=True)
        )

        # Try original name first (useful when copying to a different folder)
        if base_name not in existing_names:
            return base_name

        counter = 1
        while True:
            suffix = 'Copy' if counter == 1 else f'Copy {counter}'
            parts = base_name.rsplit('.', 1)
            if len(parts) == 2 and node_type == File.NodeType.FILE:
                candidate = f"{parts[0]} ({suffix}).{parts[1]}"
            else:
                candidate = f"{base_name} ({suffix})"

            if candidate not in existing_names:
                return candidate
            counter += 1
