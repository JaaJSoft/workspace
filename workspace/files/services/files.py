"""Centralized file management service.

All file/folder business logic lives here so it can be reused by the API
serializers, the sync service, future WebDAV integration, or any other module.
"""

import enum
import gc
import logging
import mimetypes
import os
import posixpath

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from workspace.files.models import File


class FilePermission(enum.IntEnum):
    """Permission levels for file access, ordered by capability.

    Using IntEnum so callers can compare levels: ``perm >= FilePermission.WRITE``.
    """
    VIEW = 10     # share-ro: read/download only
    WRITE = 20    # share-rw: update content only
    EDIT = 30     # group member: full CRUD
    MANAGE = 40   # owner: full control + share management

logger = logging.getLogger(__name__)


class FileService:
    """Stateless service that encapsulates file/folder business logic."""

    # ------------------------------------------------------------------
    # Querysets
    # ------------------------------------------------------------------

    @staticmethod
    def accessible_files_q(user):
        """Return a Q filter matching all files *user* can access.

        Covers owned files, group files, and individually shared files.
        Does NOT filter on ``deleted_at`` — callers add that when needed.
        """
        from django.db.models import Q
        return (
            Q(owner=user)
            | Q(group__in=user.groups.all())
            | Q(shares__shared_with=user)
        )

    @staticmethod
    def user_files_qs(user):
        """Return a queryset of active (non-deleted) personal files owned by the user."""
        return File.objects.filter(owner=user, group__isnull=True, deleted_at__isnull=True)

    @staticmethod
    def user_group_files_qs(user):
        """Return a queryset of active group files the user can access."""
        return File.objects.filter(
            group__in=user.groups.all(),
            deleted_at__isnull=True,
        )

    @staticmethod
    def storage_used(user):
        """Return total bytes used by *user*'s non-deleted files."""
        from django.db.models import Sum
        total = File.objects.filter(
            owner=user,
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
        ).aggregate(total=Sum("size"))["total"]
        return total or 0

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @staticmethod
    def create_file(owner, name, parent=None, *, content=None, mime_type=None, group=None):
        """Create a new file record, optionally with uploaded content.

        Args:
            owner: User instance.
            name: Display name.
            parent: Parent File (folder) or None for root.
            content: An uploaded file object (UploadedFile / ContentFile) or None.
            mime_type: Explicit MIME type. Inferred from *content* / *name* when omitted.
            group: Group instance to associate with the file, or None. Auto-inherited
                from *parent* when not provided.

        Returns:
            The saved File instance.
        """
        if group is None and parent and parent.group_id:
            group = parent.group

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
            group=group,
        )
        if content is not None:
            file_obj.content = content
        file_obj.save()
        return file_obj

    @staticmethod
    def create_folder(owner, name, parent=None, *, icon=None, color=None, group=None):
        """Create a new folder record.

        Also creates the directory on the storage backend when supported
        (e.g. ``FileSystemStorage``).

        Args:
            group: Group instance to associate with the folder, or None. Auto-inherited
                from *parent* when not provided.

        Returns:
            The saved File instance (node_type=FOLDER).
        """
        if group is None and parent and parent.group_id:
            group = parent.group

        folder = File(
            owner=owner,
            name=name,
            node_type=File.NodeType.FOLDER,
            parent=parent,
            icon=icon,
            color=color,
            group=group,
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
    @transaction.atomic
    def move(file_obj, new_parent, *, acting_user=None):
        """Move a file or folder to a new parent, handling physical storage.

        Must be called BEFORE the parent is updated on the instance.
        The caller (serializer) handles updating the parent field and saving.
        """
        old_parent_id = file_obj.parent_id
        new_parent_id = new_parent.pk if new_parent else None
        if old_parent_id == new_parent_id:
            return

        new_group = new_parent.group if new_parent else None
        old_group = file_obj.group

        # Determine new owner for storage path computation
        new_owner = acting_user if (old_group and not new_group and acting_user) else None

        if file_obj.node_type == File.NodeType.FOLDER:
            FileService._move_folder_storage(file_obj, new_parent, new_owner=new_owner)
        else:
            if file_obj.content and file_obj.content.name:
                FileService._move_file_storage(file_obj, new_parent, new_owner=new_owner)

        # Update parent in DB before propagating group to avoid unique constraint
        # violations (e.g. unique_group_root_folder).
        file_obj.parent = new_parent
        file_obj.save()

        # Propagate group change
        if (old_group and old_group != new_group) or (not old_group and new_group):
            FileService.propagate_group(file_obj, new_group)

        # Update owner when moving from group to personal
        if old_group and not new_group and new_owner:
            File.objects.filter(file_obj._descendant_filter()).update(owner=new_owner)
            file_obj.owner = new_owner

    @staticmethod
    def propagate_group(file_obj, group):
        """Set group on file_obj and all its descendants."""
        File.objects.filter(file_obj._descendant_filter()).update(group=group)
        file_obj.group = group

    @staticmethod
    @transaction.atomic
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
        else:
            if file_obj.content and file_obj.content.name:
                FileService._rename_file_storage(file_obj, new_name)
            file_obj.mime_type = FileService.infer_mime_type(new_name)

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
        file_obj.has_thumbnail = False
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
    # Access control
    # ------------------------------------------------------------------

    @staticmethod
    def get_permission(user, file_obj):
        """Return the access permission level for *user* on *file_obj*.

        Checks ownership, group membership, and individual shares in order.
        Only non-deleted files are considered accessible.

        Returns:
            A :class:`FilePermission` value, or ``None`` if no access.
        """
        from workspace.files.models import FileShare

        if file_obj.owner_id == user.id:
            return FilePermission.MANAGE
        if file_obj.deleted_at is not None:
            return None
        if file_obj.group_id and user.groups.filter(id=file_obj.group_id).exists():
            return FilePermission.EDIT
        share = FileShare.objects.filter(
            file=file_obj, shared_with=user,
        ).values_list('permission', flat=True).first()
        if share is None:
            return None
        return FilePermission.WRITE if share == 'rw' else FilePermission.VIEW

    @staticmethod
    def can_access(user, file_obj):
        """Check whether *user* can access *file_obj*.

        Shortcut for ``get_permission(user, file_obj) is not None``.
        """
        return FileService.get_permission(user, file_obj) is not None

    # ------------------------------------------------------------------
    # Validation (raise ValueError on failure)
    # ------------------------------------------------------------------

    @staticmethod
    def check_name_available(owner, parent, name, node_type, *, exclude_pk=None):
        """Raise ``ValueError`` if a file with the same name already exists.

        Only enforced for *files* (not folders), case-insensitive, ignoring
        soft-deleted records.  For group folders, uniqueness is scoped to
        the group rather than the owner.
        """
        if node_type != File.NodeType.FILE:
            return

        qs = File.objects.filter(
            parent=parent,
            node_type=File.NodeType.FILE,
            name__iexact=name,
            deleted_at__isnull=True,
        )
        if parent and parent.group_id:
            qs = qs.filter(group=parent.group)
        else:
            qs = qs.filter(owner=owner)

        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if qs.exists():
            raise ValueError('A file with the same name already exists in this folder.')

    @staticmethod
    def validate_move_target(file_obj, new_parent, user=None):
        """Raise ``ValueError`` if *new_parent* is an invalid move target.

        Checks:
        - Cannot move to a folder owned by another user (unless group folder).
        - Cannot move a folder into itself.
        - Cannot move a folder into one of its descendants.
        - Must be a member of the target group folder.
        """
        if new_parent is None:
            return

        if file_obj.node_type == File.NodeType.FOLDER:
            if new_parent.pk == file_obj.pk:
                raise ValueError('Cannot move a folder into itself.')
            file_path = file_obj.path or file_obj.get_path()
            parent_path = new_parent.path or new_parent.get_path()
            if parent_path.startswith(f"{file_path}/"):
                raise ValueError('Cannot move a folder into one of its descendants.')

        if new_parent.group_id:
            if user and not user.groups.filter(id=new_parent.group_id).exists():
                raise ValueError('You are not a member of this group.')
        else:
            effective_user_id = user.id if user else file_obj.owner_id
            if new_parent.owner_id != effective_user_id:
                raise ValueError('Cannot move to a folder owned by another user.')

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
    # Reading
    # ------------------------------------------------------------------

    @staticmethod
    def read_text_content(file_obj, *, max_bytes=32_768):
        """Read and return the text content of a file.

        Args:
            file_obj: A File instance (must be a FILE, not a FOLDER).
            max_bytes: Maximum number of bytes to read (default 32 KB).

        Returns:
            The decoded text content (truncated to *max_bytes*), or None
            if the file has no stored content or cannot be decoded as text.
        """
        if file_obj.node_type != File.NodeType.FILE:
            return None
        if not file_obj.content or not file_obj.content.name:
            return None
        try:
            with file_obj.content.open('rb') as fh:
                raw = fh.read(max_bytes)
            return raw.decode('utf-8')
        except (OSError, UnicodeDecodeError):
            return None

    _IMAGE_MIME_PREFIXES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml')

    @staticmethod
    def read_image_content(file_obj, *, max_bytes=10_485_760):
        """Read and return the raw bytes of an image file.

        Args:
            file_obj: A File instance whose ``mime_type`` starts with ``image/``.
            max_bytes: Maximum number of bytes to read (default 10 MB).

        Returns:
            A ``(raw_bytes, mime_type)`` tuple, or ``(None, None)`` if the
            file is not an image, has no stored content, or cannot be read.
        """
        if file_obj.node_type != File.NodeType.FILE:
            return None, None
        if not file_obj.content or not file_obj.content.name:
            return None, None
        mime = file_obj.mime_type or ''
        if not mime.startswith('image/'):
            return None, None
        try:
            with file_obj.content.open('rb') as fh:
                raw = fh.read(max_bytes)
            return raw, mime
        except OSError:
            return None, None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rename_file_storage(file_obj, new_name):
        """Rename a single file on disk."""
        old_path = file_obj.content.name
        dir_path = posixpath.dirname(old_path)

        _, ext = posixpath.splitext(old_path)
        if '.' not in new_name and ext:
            new_filename = f"{new_name}{ext}"
        else:
            new_filename = new_name
        new_path = posixpath.join(dir_path, new_filename)

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
        """Rename a folder's directory on storage and update descendant paths.

        Tries an ``os.rename`` first (single OS call, works for empty or
        populated directories).  Then walks descendants to fix up the
        ``content.name`` stored in the DB — no file I/O needed since the
        physical files were already moved.
        """
        storage_path = FileService._folder_storage_path(folder)
        new_storage_path = posixpath.join(posixpath.dirname(storage_path), new_folder_name)

        try:
            old_full = default_storage.path(storage_path)
            new_full = default_storage.path(new_storage_path)
            if os.path.isdir(old_full):
                os.rename(old_full, new_full)
        except NotImplementedError:
            pass
        except OSError as e:
            logger.warning("Could not rename folder '%s' -> '%s': %s",
                           storage_path, new_storage_path, e)

        # Update content.name in the DB for every descendant file.
        FileService._update_descendant_content_names(
            folder, old_folder_name, new_folder_name,
        )

    @staticmethod
    def _folder_storage_path(folder):
        """Return the storage-relative directory path for *folder*.

        Uses the pre-computed ``folder.path`` to avoid walking the parent
        chain.  Group folders are stored under ``files/groups/<group_name>/...``.
        Personal folders are stored under ``files/users/<username>/...``.
        """
        path = folder.path or folder.get_path()
        if folder.group_id:
            return posixpath.join('files', 'groups', folder.group.name, *path.split('/'))
        return posixpath.join('files', 'users', folder.owner.username, *path.split('/'))

    @staticmethod
    def _update_descendant_content_names(folder, old_seg, new_seg):
        """Fix ``content.name`` for all descendant files after a rename.

        Uses the stored ``path`` field to find all descendants in a single
        query, then applies a bulk update — avoiding the previous recursive
        per-folder SELECT + per-file ``save()`` overhead.
        """
        folder_path = folder.path or folder.get_path()
        descendants = list(
            File.objects.filter(
                path__startswith=f"{folder_path}/",
                node_type=File.NodeType.FILE,
            ).exclude(content='').exclude(content__isnull=True)
        )

        updated = []
        for child in descendants:
            if not child.content.name:
                continue
            segments = child.content.name.split('/')
            for i, seg in enumerate(segments):
                if seg == old_seg:
                    segments[i] = new_seg
                    break
            new_path = '/'.join(segments)
            if new_path != child.content.name:
                child.content.name = new_path
                updated.append(child)

        if updated:
            File.objects.bulk_update(updated, ['content'], batch_size=500)

    @staticmethod
    def _ensure_folder_on_storage(folder):
        """Create the folder's directory on the storage backend if supported.

        Uses the same path hierarchy as ``file_upload_path``
        (``files/<username>/<parent…>/<name>``).  Silently ignored for
        backends that don't expose a local path (e.g. S3).
        """
        storage_path = FileService._folder_storage_path(folder)
        try:
            full_path = default_storage.path(storage_path)
            os.makedirs(full_path, exist_ok=True)
        except NotImplementedError:
            pass

    @staticmethod
    def _copy_node(node, parent, owner, _sibling_names=None):
        """Recursively copy a single node.

        *_sibling_names* is a mutable set of names already present in
        *parent*.  When ``None`` (top-level call) the set is loaded once
        from the DB.  For recursive children the caller passes an empty
        set (the target folder was just created) so no extra query is
        needed.
        """
        if _sibling_names is None:
            _sibling_names = set(
                File.objects.filter(
                    owner=owner,
                    parent=parent,
                    deleted_at__isnull=True,
                ).values_list('name', flat=True)
            )

        new_name = FileService._unique_copy_name(
            node.name, node.node_type, _sibling_names,
        )
        _sibling_names.add(new_name)

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
            # The folder was just created — no children yet, so start empty.
            child_names = set()
            for child in File.objects.filter(parent=node, deleted_at__isnull=True):
                FileService._copy_node(child, copied, owner, child_names)

        return copied

    @staticmethod
    def _parent_storage_path(owner, parent):
        """Return the storage-relative directory for *parent* (or user root)."""
        if parent:
            return FileService._folder_storage_path(parent)
        return posixpath.join('files', 'users', owner.username)

    @staticmethod
    def _move_folder_storage(folder, new_parent, *, new_owner=None):
        """Move a folder directory on storage and update descendant content paths."""
        old_storage_path = FileService._folder_storage_path(folder)
        effective_owner = new_owner or folder.owner
        new_parent_storage = FileService._parent_storage_path(effective_owner, new_parent)
        new_storage_path = posixpath.join(new_parent_storage, folder.name)

        if old_storage_path == new_storage_path:
            return

        # Move directory on disk
        try:
            old_full = default_storage.path(old_storage_path)
            new_full = default_storage.path(new_storage_path)
            if os.path.isdir(old_full):
                os.makedirs(os.path.dirname(new_full), exist_ok=True)
                os.rename(old_full, new_full)
        except NotImplementedError:
            pass
        except OSError as e:
            logger.warning("Could not move folder '%s' -> '%s': %s",
                           old_storage_path, new_storage_path, e)

        # Update content.name for all descendant files
        folder_path = folder.path or folder.get_path()
        descendants = list(
            File.objects.filter(
                path__startswith=f"{folder_path}/",
                node_type=File.NodeType.FILE,
            ).exclude(content='').exclude(content__isnull=True)
        )

        old_prefix = old_storage_path.replace('\\', '/')
        new_prefix = new_storage_path.replace('\\', '/')

        updated = []
        for child in descendants:
            if not child.content.name:
                continue
            content_name = child.content.name.replace('\\', '/')
            if content_name.startswith(old_prefix + '/'):
                child.content.name = new_prefix + content_name[len(old_prefix):]
                updated.append(child)

        if updated:
            File.objects.bulk_update(updated, ['content'], batch_size=500)

    @staticmethod
    def _move_file_storage(file_obj, new_parent, *, new_owner=None):
        """Move a single file on storage to a new parent directory."""
        old_path = file_obj.content.name
        effective_owner = new_owner or file_obj.owner
        new_parent_storage = FileService._parent_storage_path(effective_owner, new_parent)
        new_path = posixpath.join(new_parent_storage, posixpath.basename(old_path))

        if old_path == new_path:
            return

        try:
            old_full = default_storage.path(old_path)
            new_full = default_storage.path(new_path)
            if os.path.isfile(old_full):
                os.makedirs(os.path.dirname(new_full), exist_ok=True)
                os.rename(old_full, new_full)
                file_obj.content.name = new_path
        except NotImplementedError:
            # Fallback for non-local storage backends
            if not default_storage.exists(old_path):
                logger.warning("File does not exist on storage: '%s'", old_path)
                return
            file_handle = None
            try:
                file_handle = default_storage.open(old_path, 'rb')
                data = file_handle.read()
            finally:
                if file_handle:
                    file_handle.close()
                    gc.collect()
            saved_path = default_storage.save(new_path, ContentFile(data))
            file_obj.content.name = saved_path
            if old_path != saved_path:
                try:
                    default_storage.delete(old_path)
                except Exception as e:
                    logger.warning("Could not delete old file '%s': %s", old_path, e)
        except OSError as e:
            logger.warning("Could not move file '%s' -> '%s': %s", old_path, new_path, e)

    @staticmethod
    def _unique_copy_name(base_name, node_type, existing_names):
        """Pick a unique name given a set of *existing_names*."""
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
