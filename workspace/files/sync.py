"""Bidirectional file sync between disk storage and database."""

import logging
import os
from dataclasses import dataclass, field

from django.core.files.storage import default_storage
from django.utils import timezone

from workspace.files.models import File
from workspace.files.services import FileService

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    files_created: int = 0
    folders_created: int = 0
    files_soft_deleted: int = 0
    folders_soft_deleted: int = 0
    errors: list[str] = field(default_factory=list)


class FileSyncService:
    """Synchronize files between disk storage and database.

    Bidirectional:
    - Disk -> DB: create DB entries for files present on disk but missing in DB.
    - DB -> Disk: soft-delete DB entries whose files no longer exist on disk.
    """

    def __init__(self, *, dry_run=False, log=None):
        self.dry_run = dry_run
        self.log = log or logger

    def sync_user_recursive(self, user) -> SyncResult:
        """Full recursive sync for a single user."""
        result = SyncResult()
        user_dir = os.path.join(default_storage.location, 'files', user.username)

        if not os.path.isdir(user_dir):
            return result

        self._sync_directory_recursive(
            user=user,
            disk_path=user_dir,
            parent_db=None,
            storage_prefix=f'files/{user.username}',
            result=result,
        )
        return result

    def sync_folder_shallow(self, user, parent_db=None) -> SyncResult:
        """Sync immediate children of a specific folder (or root if None)."""
        result = SyncResult()

        if parent_db is None:
            disk_path = os.path.join(default_storage.location, 'files', user.username)
            storage_prefix = f'files/{user.username}'
        else:
            disk_path = os.path.join(
                default_storage.location, 'files', user.username,
                *parent_db.path.split('/') if parent_db.path else [parent_db.name],
            )
            storage_prefix = f'files/{user.username}/{parent_db.path or parent_db.name}'

        if not os.path.isdir(disk_path):
            return result

        self._sync_one_level(user, disk_path, parent_db, storage_prefix, result)
        return result

    def _sync_directory_recursive(self, user, disk_path, parent_db, storage_prefix, result):
        """Sync one directory level, then recurse into subdirectories."""
        self._sync_one_level(user, disk_path, parent_db, storage_prefix, result)

        try:
            entries = list(os.scandir(disk_path))
        except OSError as e:
            result.errors.append(f"Cannot scan {disk_path}: {e}")
            return

        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue

            folder_db = File.objects.filter(
                owner=user,
                parent=parent_db,
                name=entry.name,
                node_type=File.NodeType.FOLDER,
                deleted_at__isnull=True,
            ).first()

            if folder_db:
                self._sync_directory_recursive(
                    user=user,
                    disk_path=entry.path,
                    parent_db=folder_db,
                    storage_prefix=f'{storage_prefix}/{entry.name}',
                    result=result,
                )

    def _sync_one_level(self, user, disk_path, parent_db, storage_prefix, result):
        """Bidirectional sync of immediate children at one directory level."""
        now = timezone.now()

        # --- Read disk entries ---
        try:
            disk_entries = list(os.scandir(disk_path))
        except OSError as e:
            result.errors.append(f"Cannot read {disk_path}: {e}")
            return

        disk_names = {}  # name -> DirEntry
        for entry in disk_entries:
            disk_names[entry.name] = entry

        # --- Read DB entries (non-deleted) at this level ---
        db_records = File.objects.filter(
            owner=user,
            parent=parent_db,
            deleted_at__isnull=True,
        )
        db_by_name = {}  # (name, node_type) -> File
        for rec in db_records:
            db_by_name[(rec.name, rec.node_type)] = rec

        # --- Phase 1: Disk -> DB (create missing) ---
        for entry_name, entry in disk_names.items():
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)

            if not is_dir and not is_file:
                continue  # skip symlinks, special files

            node_type = File.NodeType.FOLDER if is_dir else File.NodeType.FILE

            if (entry_name, node_type) in db_by_name:
                continue  # already tracked

            if self.dry_run:
                self.log.info("[DRY-RUN] Would create %s: %s", node_type, entry_name)
                if is_dir:
                    result.folders_created += 1
                else:
                    result.files_created += 1
                continue

            try:
                if is_dir:
                    FileService.create_folder(user, entry_name, parent_db)
                    result.folders_created += 1
                    self.log.info("Created folder: %s", entry_name)
                else:
                    content_path = f'{storage_prefix}/{entry_name}'
                    mime_type = FileService.infer_mime_type(entry_name)

                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = None

                    FileService.register_disk_file(
                        user, entry_name, parent_db, content_path,
                        mime_type=mime_type, size=size,
                    )
                    result.files_created += 1
                    self.log.info("Created file: %s (%s, %s bytes)", entry_name, mime_type, size)

            except Exception as e:
                result.errors.append(f"Error creating {entry_name}: {e}")
                self.log.warning("Error creating %s: %s", entry_name, e)

        # --- Phase 2: DB -> Disk (soft-delete orphans) ---
        for (name, node_type), db_record in db_by_name.items():
            if name in disk_names:
                disk_entry = disk_names[name]
                is_dir = disk_entry.is_dir(follow_symlinks=False)
                expected_type = File.NodeType.FOLDER if is_dir else File.NodeType.FILE
                if expected_type == node_type:
                    continue  # matches, nothing to do

            # Not found on disk or type mismatch -> soft-delete
            if self.dry_run:
                self.log.info("[DRY-RUN] Would soft-delete %s: %s", node_type, name)
                if node_type == File.NodeType.FOLDER:
                    result.folders_soft_deleted += 1
                else:
                    result.files_soft_deleted += 1
                continue

            try:
                count = db_record.soft_delete(deleted_at=now)
                self.log.info("Soft-deleted %s: %s (%d records)", node_type, name, count)
                if node_type == File.NodeType.FOLDER:
                    result.folders_soft_deleted += 1
                else:
                    result.files_soft_deleted += 1
            except Exception as e:
                result.errors.append(f"Error soft-deleting {name}: {e}")
                self.log.warning("Error soft-deleting %s: %s", name, e)

