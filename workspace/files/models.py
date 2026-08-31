import posixpath
import secrets

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Concat, Lower, Substr
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from workspace.common.uuids import uuid_v7_or_v4

User = get_user_model()


def file_upload_path(instance, filename):
    """Generate upload path based on the node's position in the tree.

    Uses ``instance.path`` (set by ``File.save()`` before
    ``super().save()`` runs) to avoid walking the parent FK chain.
    Group files are stored under ``files/groups/<group_name>/...``.
    Personal files are stored under ``files/users/<username>/...``.
    """
    if instance.group_id:
        root = "files/groups"
    else:
        root = "files/users/" + instance.owner.username

    if instance.path:
        parent_parts = instance.path.split("/")[:-1]
        return posixpath.join(root, *parent_parts, filename)
    return posixpath.join(root, filename)


class FileQuerySet(models.QuerySet):
    def name_ordered(self, *prefix_fields):
        """Sort by name case-insensitively, with optional leading fields.

        Usage:
            qs.name_ordered()                    # ORDER BY LOWER(name)
            qs.name_ordered('-node_type')         # ORDER BY node_type DESC, LOWER(name)
            qs.name_ordered('-deleted_at')        # ORDER BY deleted_at DESC, LOWER(name)
        """
        return self.order_by(*prefix_fields, Lower("name"))


class File(models.Model):
    """Model representing a file or folder in a tree structure."""

    class NodeType(models.TextChoices):
        FILE = "file", "File"
        FOLDER = "folder", "Folder"

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    name = models.CharField(max_length=255)
    node_type = models.CharField(max_length=10, choices=NodeType.choices)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )

    # File-specific fields
    content = models.FileField(
        upload_to=file_upload_path,
        storage=FileSystemStorage(allow_overwrite=True),
        null=True,
        blank=True,
        max_length=1024,
    )
    size = models.BigIntegerField(null=True, blank=True, help_text="File size in bytes")
    mime_type = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    type = models.CharField(max_length=50, default="unknown", db_index=True)
    category = models.CharField(max_length=20, default="unknown", db_index=True)
    # Pinned viewer slug. Empty means "derive from the content type", which is
    # the common case. Set when the content type alone cannot tell which
    # renderer applies, e.g. an audio-only MP4 container.
    viewer = models.CharField(max_length=32, blank=True, default="")
    # SHA-256 hex digest of the blob, refreshed on every content write. Empty
    # for folders and for rows registered before the hash existed (see the
    # backfill_file_hashes command). Only used to spot duplicate uploads;
    # rows never share a blob on the strength of it.
    content_hash = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )

    has_thumbnail = models.BooleanField(default=False)

    # Key the SQLite full-text index is built on (see DerivedFulltextIndex).
    # FTS5 rowids are integers and the index cannot be rebuilt from the
    # database, so it cannot ride on the implicit rowid: Django rebuilds this
    # table on any AddField and SQLite reassigns those. Derived from the uuid
    # by the indexing task, unused on PostgreSQL.
    fts_rowid = models.BigIntegerField(
        null=True, blank=True, unique=True, editable=False
    )

    # Folder customization
    icon = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Custom Lucide icon name for folders (e.g., 'briefcase', 'heart')",
    )
    color = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        help_text="Custom color class for folder icon (e.g., 'text-error', 'text-success')",
    )

    path = models.TextField(
        blank=True, editable=False, help_text="Full path from root to this node."
    )

    # Metadata
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="files")
    group = models.ForeignKey(
        "auth.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="group_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Locking
    locked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locked_files",
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    lock_expires_at = models.DateTimeField(null=True, blank=True)
    # Session token of the current lock. Protocols that identify their locks
    # by an opaque token (WOPI today; WebDAV-style tokens fit too, up to the
    # 1024 chars the WOPI spec allows) store it here; the in-app editor
    # heartbeat locks without one and leaves it empty.
    lock_token = models.CharField(max_length=1024, blank=True, default="")

    objects = FileQuerySet.as_manager()

    class Meta:
        ordering = ["node_type", Lower("name")]
        indexes = [
            models.Index(fields=["parent", "node_type"]),
            models.Index(fields=["owner", "created_at"]),
            models.Index(fields=["owner", "deleted_at"], name="file_owner_del_idx"),
            models.Index(fields=["group", "deleted_at"], name="file_group_del_idx"),
            models.Index(fields=["locked_by", "lock_expires_at"], name="file_lock_idx"),
            models.Index(
                fields=["owner", "deleted_at", "node_type"],
                include=["size"],
                name="file_owner_del_type_size",
            ),
            # Serves quota.personal_usage: SUM(size) over one owner's live and
            # trashed personal files. Partial on `group IS NULL` because the
            # bucket excludes group files, and covering on `size` so the sum
            # never touches the heap (PostgreSQL; SQLite ignores INCLUDE).
            models.Index(
                fields=["owner", "node_type"],
                include=["size"],
                condition=Q(group__isnull=True),
                name="file_personal_usage",
            ),
            # Same, for quota.group_usage.
            models.Index(
                fields=["group", "node_type"],
                include=["size"],
                condition=Q(group__isnull=False),
                name="file_group_usage",
            ),
            models.Index(
                fields=["parent", "deleted_at", "name"], name="file_parent_del_name"
            ),
            # Serves the "Recent" views: filter (owner, deleted_at IS NULL)
            # then ORDER BY updated_at DESC, LOWER(name) with a small LIMIT.
            # The full ORDER BY is in the index (including the tiebreak
            # expression) so SQLite can early-exit at the limit instead of
            # sorting every live file the user owns; the API variant that
            # orders by updated_at alone is served by the same prefix.
            models.Index(
                F("owner"),
                F("deleted_at"),
                F("updated_at").desc(),
                Lower("name"),
                name="file_owner_del_recent",
            ),
            # `text_pattern_ops` makes this index usable for `path__startswith`
            # under non-C UTF-8 collations (PostgreSQL). Silently ignored on SQLite,
            # which falls back to a regular B-tree (also usable for prefix LIKE).
            models.Index(
                fields=["path"],
                name="file_path_idx",
                opclasses=["text_pattern_ops"],
            ),
            # Partial index for the thumbnail backfill scan: only the few
            # un-thumbnailed live files, so the periodic query never scans the
            # whole table.
            models.Index(
                fields=["type"],
                # node_type matches the backfill query (files only, never
                # folders, which would otherwise bloat the partial index).
                condition=Q(
                    has_thumbnail=False,
                    deleted_at__isnull=True,
                    node_type="file",  # File.NodeType.FILE
                ),
                name="file_thumb_pending_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(node_type="folder")
                        & (models.Q(content__isnull=True) | models.Q(content=""))
                    )
                    | models.Q(node_type="file")
                ),
                name="folder_has_no_content",
            ),
            models.UniqueConstraint(
                fields=["group"],
                condition=models.Q(
                    group__isnull=False,
                    parent__isnull=True,
                    deleted_at__isnull=True,
                ),
                name="unique_group_root_folder",
            ),
        ]

    def __str__(self):
        return f"{self.get_node_type_display()}: {self.name}"

    def save(self, *args, **kwargs):
        if "/" in self.name:
            raise ValueError("File and folder names must not contain '/'.")
        # '.'/'..' would resolve to a parent directory in every storage path
        # built from ``path``, letting a rename or delete escape the node's
        # own directory.
        if self.name in (".", ".."):
            raise ValueError("File and folder names must not be '.' or '..'.")

        old_data = None
        if self.pk:
            old_data = (
                File.objects.filter(pk=self.pk)
                .values("name", "parent_id", "path")
                .first()
            )

        new_path = self._build_path_for(self.name, self.parent_id)
        self.path = new_path

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"path"}

        if old_data:
            old_path = old_data.get("path")
            if not old_path:
                old_path = self._build_path_for(old_data["name"], old_data["parent_id"])
            if old_path and old_path != new_path:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                    self._update_descendant_paths(old_path, new_path)
                return

        super().save(*args, **kwargs)

    @classmethod
    def _update_descendant_paths(cls, old_path, new_path):
        prefix = f"{old_path}/"
        start_pos = len(old_path) + 2
        cls.objects.filter(path__startswith=prefix).update(
            path=Concat(Value(f"{new_path}/"), Substr("path", start_pos))
        )

    @classmethod
    def _build_path_for(cls, name, parent_id):
        if parent_id:
            parent = cls.objects.only("path", "name", "parent_id").get(pk=parent_id)
            parent_path = parent.path or parent.get_path()
            return f"{parent_path}/{name}"
        return name

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
        from workspace.files.services.filetype import is_viewable, label_from_mime

        if self.node_type != self.NodeType.FILE:
            return False
        label = (
            self.type
            if self.type and self.type != "unknown"
            else label_from_mime(self.mime_type or "")
        )
        return is_viewable(label, self.name or "")

    def is_quarantined(self):
        """True when the malware policy currently denies access to this file.

        Reads ``self.scan``; listing querysets apply ``policy.with_scan`` so
        the template loop does not issue one query per row.
        """
        from workspace.files.services.scanning.policy import is_blocked

        return is_blocked(self)

    def is_deleted(self):
        return self.deleted_at is not None

    def is_locked(self):
        """Return True if this file has an active (non-expired) lock."""
        if self.locked_by_id is None:
            return False
        return self.lock_expires_at and self.lock_expires_at > timezone.now()

    def lock_holder_username(self):
        """Return the username of the lock holder, or None."""
        if self.is_locked() and self.locked_by:
            return self.locked_by.username
        return None

    def _descendant_filter(self):
        if self.node_type != self.NodeType.FOLDER:
            return models.Q(pk=self.pk)
        path = self.path or self.get_path()
        if not path:
            return models.Q(pk=self.pk)
        prefix = f"{path}/"
        return models.Q(pk=self.pk) | models.Q(path__startswith=prefix)

    @transaction.atomic
    def soft_delete(self, deleted_at=None):
        """Trash this node and its subtree, moving the bytes out of the tree.

        The storage move is last: the name a trashed node used to occupy has
        to be free for the next file to claim, and leaving the bytes there
        would let that file overwrite them.
        """
        # Imported lazily: the services package imports this module.
        from workspace.files.services import _storage_ops as _storage

        if deleted_at is None:
            deleted_at = timezone.now()

        source = _storage.current_node_path(self)
        count = File.objects.filter(
            self._descendant_filter(),
            deleted_at__isnull=True,
        ).update(deleted_at=deleted_at)
        if not count:
            return 0

        self.deleted_at = deleted_at
        if source:
            _storage.move_node_storage(self, source, _storage.trash_node_path(self))
        return count

    def _restore_parents(self):
        parent_id = self.parent_id
        restored_ids = []
        while parent_id:
            parent = (
                File.objects.filter(pk=parent_id)
                .values("pk", "parent_id", "deleted_at")
                .first()
            )
            if not parent or parent["deleted_at"] is None:
                break
            restored_ids.append(parent["pk"])
            parent_id = parent["parent_id"]
        if restored_ids:
            File.objects.filter(pk__in=restored_ids).update(deleted_at=None)
        return len(restored_ids)

    @transaction.atomic
    def restore(self):
        """Bring this node back into the live tree, bytes included.

        The node that physically moves is the outermost trashed node of the
        chain - restoring a file whose folder was trashed brings the folder
        back too, which is what ``_restore_parents`` already did in the
        database.
        """
        from workspace.files.services import _storage_ops as _storage
        from workspace.files.services._names import (
            available_node_name,
            find_node_conflict,
        )
        from workspace.files.services._trash import trash_root_of

        if self.deleted_at is None and self.node_type != self.NodeType.FOLDER:
            return 0

        root = trash_root_of(self)
        if root is not None and root.pk == self.pk:
            # Same row: work through one instance, or the rename below and
            # the save() after it would fight over ``name`` and ``path``.
            root = self
        source = _storage.current_node_path(root) if root is not None else None

        if root is not None and find_node_conflict(
            root.owner, root.parent, root.name, exclude_pk=root.pk
        ):
            # Something took the name while the node sat in the trash.
            root.name = available_node_name(
                root.owner, root.parent, root.name, root.node_type
            )
            root.save(update_fields=["name"])

        if self.node_type == self.NodeType.FOLDER:
            updated = File.objects.filter(self._descendant_filter()).update(
                deleted_at=None
            )
        else:
            self.deleted_at = None
            self.save(update_fields=["deleted_at"])
            updated = 1
        self._restore_parents()

        if root is not None and source:
            root.deleted_at = None
            _storage.move_node_storage(root, source, _storage.live_node_path(root))
            _storage.reconcile_trashed_children(root)
        return updated

    def delete(self, *args, **kwargs):
        """Soft-delete by default; pass hard=True to permanently delete."""
        hard = kwargs.pop("hard", False)
        if hard:
            return super().delete(*args, **kwargs)
        return self.soft_delete()


class FileFavorite(models.Model):
    """User favorites for files or folders."""

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="file_favorites",
    )
    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "file"],
                name="unique_file_favorite",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "created_at"], name="file_fav_owner_created"),
        ]

    def __str__(self):
        return f"{self.owner} -> {self.file}"


class FileShare(models.Model):
    """Share a file or folder with another user."""

    class Permission(models.TextChoices):
        READ_ONLY = "ro", "Read only"
        READ_WRITE = "rw", "Read & write"

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="shares",
    )
    shared_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="shared_files",
    )
    shared_with = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="received_shares",
    )
    permission = models.CharField(
        max_length=2,
        choices=Permission.choices,
        default=Permission.READ_ONLY,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["file", "shared_with"],
                name="unique_file_share",
            ),
        ]
        indexes = [
            models.Index(
                fields=["shared_with", "created_at"], name="file_share_recv_idx"
            ),
            models.Index(
                fields=["shared_by", "created_at"], name="file_share_sent_idx"
            ),
        ]

    def __str__(self):
        return f"{self.shared_by} -> {self.shared_with}: {self.file}"


class FileComment(models.Model):
    """User comment on a file or folder."""

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="file_comments",
    )
    body = models.TextField()
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["file", "created_at"], name="file_comment_file_created"
            ),
            models.Index(fields=["deleted_at"], name="file_comment_deleted_at"),
        ]

    def __str__(self):
        return f"{self.author} on {self.file} ({self.created_at})"


class PinnedFolder(models.Model):
    """User-pinned folders for quick sidebar access."""

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="pinned_folders",
    )
    folder = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="pins",
        limit_choices_to={"node_type": "folder"},
    )
    position = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "folder"],
                name="unique_pinned_folder",
            ),
        ]
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(fields=["owner", "position"], name="pinned_owner_pos"),
        ]

    def __str__(self):
        return f"{self.owner} -> {self.folder}"


@receiver(pre_delete, sender=File)
def delete_file_on_delete(sender, instance, **kwargs):
    """Remove the physical file or folder when a File row is deleted.

    A pre_delete signal (rather than an override of ``delete``) so the disk
    cleanup also runs on queryset and cascade deletes.
    """
    # Imported lazily: the services package imports this module.
    from workspace.files.services._storage_ops import delete_node_storage

    delete_node_storage(instance)


@receiver(pre_delete, sender=File)
def unindex_file_on_delete(sender, instance, **kwargs):
    """Drop the file's search document before its row goes away.

    pre_delete rather than post_delete: on SQLite the contentless FTS5 table
    is keyed on the base row's rowid, which is only resolvable while the row
    still exists. Runs on queryset and cascade deletes for the same reason as
    the storage cleanup above.
    """
    from workspace.files.services.search_index import unindex_file

    unindex_file(instance)


@receiver(pre_delete, sender="auth.Group")
def soft_delete_group_files(sender, instance, **kwargs):
    """Soft-delete all files belonging to this group before it is deleted.

    Through the root folders rather than in one queryset update: trashing a
    node moves its bytes out of the live tree, and only the model method
    does that.
    """
    now = timezone.now()
    roots = File.objects.filter(
        group=instance,
        deleted_at__isnull=True,
        parent__isnull=True,
    ).select_related("owner")
    for root in roots:
        root.soft_delete(deleted_at=now)
    # Anything the group owns outside those roots (legacy rows re-parented by
    # hand) still has to leave the live listing.
    File.objects.filter(group=instance, deleted_at__isnull=True).update(deleted_at=now)


def _generate_share_link_token():
    return secrets.token_urlsafe(24)


class FileShareLink(models.Model):
    """A public share link for a file, allowing unauthenticated access."""

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="share_links")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    token = models.CharField(
        max_length=44, unique=True, default=_generate_share_link_token
    )
    password = models.CharField(max_length=128, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ShareLink({self.token[:8]}... -> {self.file.name})"

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        from django.utils import timezone

        return self.expires_at <= timezone.now()

    @property
    def has_password(self):
        return bool(self.password)


class Tag(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=50, blank=True, default="")
    # A CSS color (the picker offers a fixed hex palette, shared with
    # projects labels). Empty renders the neutral chip.
    color = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = [Lower("name")]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="unique_tag_per_user"
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "name"]),
        ]

    def __str__(self):
        return self.name


class FileTag(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="file_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="file_tags")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["file", "tag"], name="unique_file_tag"),
        ]
        indexes = [
            models.Index(fields=["file", "tag"]),
        ]

    def __str__(self):
        return f"{self.file.name} — {self.tag.name}"


class FileEvent(models.Model):
    """Audit log entry for non-read operations performed on a file or folder."""

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        RENAMED = "renamed", "Renamed"
        MOVED = "moved", "Moved"
        CONTENT_REPLACED = "content_replaced", "Updated"
        DELETED = "deleted", "Trashed"
        RESTORED = "restored", "Restored"
        SHARED = "shared", "Shared"
        SHARE_PERMISSION_CHANGED = "share_permission_changed", "Permission changed"
        UNSHARED = "unshared", "Unshared"
        LINK_CREATED = "link_created", "Link created"
        LINK_REVOKED = "link_revoked", "Link revoked"

    # Single source of truth for the per-action presentation metadata
    # (Lucide icon + category used for grouping in the filter dropdown).
    _ACTION_METADATA = {
        Action.CREATED: ("plus-circle", "Lifecycle"),
        Action.DELETED: ("trash-2", "Lifecycle"),
        Action.RESTORED: ("rotate-ccw", "Lifecycle"),
        Action.RENAMED: ("pencil", "Edits"),
        Action.MOVED: ("move", "Edits"),
        Action.CONTENT_REPLACED: ("upload", "Edits"),
        Action.SHARED: ("user-plus", "Sharing"),
        Action.SHARE_PERMISSION_CHANGED: ("shield", "Sharing"),
        Action.UNSHARED: ("user-minus", "Sharing"),
        Action.LINK_CREATED: ("link", "Sharing"),
        Action.LINK_REVOKED: ("unlink", "Sharing"),
    }

    # Display order for the action-filter dropdown's optgroups.
    _CATEGORY_ORDER = ["Lifecycle", "Edits", "Sharing"]

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="file_events",
        help_text="User who performed the action. Null for system actions.",
    )
    action = models.CharField(max_length=32, choices=Action.choices, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["file", "-created_at"], name="file_event_file_created"
            ),
        ]

    def __str__(self):
        return (
            f"{self.action} on {self.file_id} by {self.actor_id} at {self.created_at}"
        )

    @property
    def icon(self):
        """Lucide icon name for this event's action."""
        return self._ACTION_METADATA.get(self.action, ("activity", "Other"))[0]

    @property
    def short_label(self):
        """Noun-form label (matches the Action.choices display label)."""
        return self.get_action_display()

    @classmethod
    def grouped_actions(cls, only=None):
        """Return ``[(category, [(value, label), ...]), ...]`` for the dropdown.

        ``only`` restricts the result to a subset of action values - typically
        the distinct actions present on a given file, so the dropdown only
        offers filters that will yield matches.
        """
        only_set = set(only) if only is not None else None
        groups: dict[str, list[tuple[str, str]]] = {}
        for value, label in cls.Action.choices:
            if only_set is not None and value not in only_set:
                continue
            _icon, category = cls._ACTION_METADATA.get(value, ("activity", "Other"))
            groups.setdefault(category, []).append((value, label))
        return [(cat, groups[cat]) for cat in cls._CATEGORY_ORDER if cat in groups]


class FileLink(models.Model):
    """A content reference (link) from one file to another - a graph edge.

    Generic: any file may reference any file. Today only the markdown extractor
    (services/links.py) populates it, from resolved ``[[`` wikilinks of the form
    ``[Title](/notes?file=UUID)``. Distinct from FileShare / FileShareLink,
    which model sharing rather than content references.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    source = models.ForeignKey(
        File, on_delete=models.CASCADE, related_name="outgoing_links"
    )
    target = models.ForeignKey(
        File, on_delete=models.CASCADE, related_name="incoming_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "target"], name="unique_file_link"
            ),
        ]
        indexes = [
            # The unique constraint already serves (source, target) lookups
            # (outgoing edges); this index serves the reverse query (backlinks).
            models.Index(fields=["target"], name="file_link_target_idx"),
        ]

    def __str__(self):
        return f"{self.source_id} -> {self.target_id}"


class ThumbnailFailure(models.Model):
    """An image file whose thumbnail generation failed.

    A row exists only while the file is failing: it is dropped as soon as
    generation succeeds or the file's content is replaced, so ``attempts``
    counts attempts against the file's current bytes. The exception is a
    backfill already decoding when the content is replaced, which records
    against the old bytes after the row was cleared and costs the new content
    an attempt or two. Parking expires within PARKED_RETRY_AFTER regardless,
    so the row carries no content revision to guard against that.
    """

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    # Must stay non-nullable: parked_file_ids() feeds a NOT IN subquery, where a
    # single NULL makes the predicate UNKNOWN for every row and silently reduces
    # the backfill to zero files.
    file = models.OneToOneField(
        File,
        on_delete=models.CASCADE,
        related_name="thumbnail_failure",
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    # Set explicitly by the service: the counter is bumped through a queryset
    # .update() for atomicity, and auto_now only fires inside Model.save().
    last_attempt_at = models.DateTimeField()
    last_error = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.file}: {self.attempts} failed thumbnail attempt(s)"


class FileScan(models.Model):
    """The most recent malware-scan verdict for a file.

    One row per scanned file. A file with no row has never been scanned:
    scanning is off, the file predates the feature, or its scan is still
    queued. There is deliberately no "pending" status - writing one would put
    a query on the upload path, and a file whose scan is in flight stays
    readable, so the absence of a row already carries that meaning.

    A content replacement does NOT clear the row; the scan of the new bytes
    overwrites it. That is the conservative direction: an infected file cannot
    be un-quarantined by overwriting it and racing the scan.
    """

    class Status(models.TextChoices):
        CLEAN = "clean", "Clean"
        INFECTED = "infected", "Infected"
        SKIPPED = "skipped", "Skipped"
        ERROR = "error", "Scan failed"

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    # Must stay non-nullable: the blocked-ids subquery feeds a NOT IN, where a
    # single NULL makes the predicate UNKNOWN for every row and would silently
    # empty every search result page.
    file = models.OneToOneField(
        File,
        on_delete=models.CASCADE,
        related_name="scan",
    )
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    signature = models.CharField(max_length=200, blank=True, default="")
    detail = models.CharField(max_length=500, blank=True, default="")
    # The File.content_hash the verdict describes, so a verdict can be told
    # apart from the bytes the file holds now. Not indexed: it is only ever
    # compared against the joined file's own hash, never looked up on its own.
    # Blank for a row written before this field existed, and for a file whose
    # own hash could not be computed - both mean "cannot vouch for these
    # bytes", which is why the backfill treats them as needing a scan.
    content_hash = models.CharField(max_length=64, blank=True, default="")
    # Set explicitly by the task: rows are written through update_or_create and
    # auto_now only fires inside Model.save().
    scanned_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(
                fields=["status", "-scanned_at"], name="file_scan_status_time"
            ),
        ]

    def __str__(self):
        return f"{self.file}: {self.status}"


class UserStorageQuota(models.Model):
    """Storage limit for one user's personal files.

    No row means the global ``STORAGE_QUOTA_BYTES`` applies; an empty
    ``quota_bytes`` means unlimited. Nothing outside the admin writes here.
    """

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="storage_quota"
    )
    quota_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Bytes allowed for personal files. Leave empty for unlimited.",
    )
    note = models.TextField(
        blank=True, help_text="Why this user deviates from the default."
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        limit = "unlimited" if self.quota_bytes is None else f"{self.quota_bytes} bytes"
        return f"{self.user}: {limit}"


class GroupStorageQuota(models.Model):
    """Storage limit for one group's folder.

    No row means unlimited, and so does an empty ``quota_bytes``.
    """

    uuid = models.UUIDField(
        primary_key=True, editable=False, unique=True, default=uuid_v7_or_v4
    )
    group = models.OneToOneField(
        "auth.Group", on_delete=models.CASCADE, related_name="storage_quota"
    )
    quota_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Bytes allowed in this group's folder. Leave empty for unlimited.",
    )
    note = models.TextField(
        blank=True, help_text="Why this group deviates from the default."
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        limit = "unlimited" if self.quota_bytes is None else f"{self.quota_bytes} bytes"
        return f"{self.group}: {limit}"
