"""Files importer: walks a remote file tree and writes it into the files module.

Both walks (listing, then copying) keep their pending stack in the job stats,
so a slice that runs out of time resumes where it stopped instead of
re-listing the whole tree.
"""

import logging
import shutil
import tempfile
from itertools import batched

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import File as DjangoFile
from django.db import DataError, IntegrityError
from django.template.defaultfilters import filesizeformat
from rest_framework import serializers

from workspace.common.logging import scrub
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services._names import available_file_name, find_name_conflict

from ..models import ImportJobItem
from ..providers.base import KIND_FILES, ProviderError
from .base import Importer, JobFailed, Outcome

logger = logging.getLogger(__name__)

ON_CONFLICT_SKIP = "skip"
ON_CONFLICT_RENAME = "rename"
ON_CONFLICT_REPLACE = "replace"

# Remote bytes are spooled before they reach FileService, which needs a
# seekable stream (type detection, hashing and the storage write are three
# passes). Small files stay in memory, bigger ones go through a temp file.
_SPOOL_MAX_MEMORY = 8 * 1024 * 1024
_QUOTA_RECHECK_EVERY = 50
_MAX_NAME_LENGTH = File._meta.get_field("name").max_length
_MAX_MIME_LENGTH = File._meta.get_field("mime_type").max_length
_STORAGE_ERRORS = (OSError, ValueError, DataError, IntegrityError, ValidationError)


class FilesImportOptionsSerializer(serializers.Serializer):
    source_path = serializers.CharField(required=False, allow_blank=True, default="/")
    destination = serializers.UUIDField(required=False, allow_null=True, default=None)
    on_conflict = serializers.ChoiceField(
        choices=[ON_CONFLICT_SKIP, ON_CONFLICT_RENAME, ON_CONFLICT_REPLACE],
        default=ON_CONFLICT_RENAME,
    )
    create_root_folder = serializers.BooleanField(default=True)

    def validate_destination(self, value):
        if value is None:
            return None
        owner = self.context["owner"]
        exists = (
            FileService.user_files_qs(owner)
            .filter(uuid=value, node_type=File.NodeType.FOLDER)
            .exists()
        )
        if not exists:
            raise serializers.ValidationError("Destination folder not found.")
        return str(value)

    def validate_source_path(self, value):
        return "/" + value.strip("/") if value.strip("/") else "/"


def safe_local_name(name: str) -> str:
    """A remote name the files module will accept as a local one."""
    cleaned = name.replace("/", "-").replace("\x00", "").strip() or "untitled"
    return cleaned[:_MAX_NAME_LENGTH]


class FilesImporter(Importer):
    kind = KIND_FILES
    option_serializer = FilesImportOptionsSerializer

    def run(self, ctx):
        source = ctx.provider.file_source(ctx.connection)
        try:
            if not ctx.stats.get("planned"):
                ctx.set_phase("listing")
                outcome = self._plan(ctx, source)
                if outcome is not None:
                    return outcome
            ctx.set_phase("copying")
            outcome = self._copy(ctx, source)
            if outcome is Outcome.DONE:
                ctx.set_phase("done")
            return outcome
        finally:
            source.close()

    def live_targets(self, owner, target_uuids):
        alive = set()
        for chunk in batched(target_uuids, 500, strict=False):
            alive.update(
                FileService.user_files_qs(owner)
                .filter(uuid__in=chunk)
                .values_list("uuid", flat=True)
            )
        return alive

    def summarize(self, stats):
        parts = []
        for key, label in (
            ("files", "files"),
            ("unchanged", "unchanged"),
            ("skipped", "skipped"),
            ("failed", "failed"),
        ):
            if stats.get(key):
                parts.append(f"{stats[key]} {label}")
        return ", ".join(parts) or "Nothing to import."

    # -- listing phase -------------------------------------------------

    def _plan(self, ctx, source):
        """Count files and bytes so the UI has a total and the quota can be
        checked before anything is written. Entries already imported count as
        files but not as bytes: they will not be fetched."""
        stack = ctx.stats.setdefault(
            "plan_stack", [ctx.options.get("source_path", "/")]
        )
        ctx.stats.setdefault("total_files", 0)
        ctx.stats.setdefault("total_bytes", 0)
        ctx.stats.setdefault("unchanged", 0)
        while stack:
            if stop := ctx.should_stop():
                ctx.flush(force=True)
                return stop
            remote_dir = stack[-1]
            subdirs = []
            try:
                for entry in source.list_dir(remote_dir):
                    if entry.is_dir:
                        subdirs.append(entry.id)
                    else:
                        ctx.stat("total_files")
                        if ctx.already_done(entry.id, entry.fingerprint):
                            ctx.stat("unchanged")
                        else:
                            ctx.stat("total_bytes", entry.size or 0)
            except ProviderError as exc:
                raise JobFailed(
                    f"Could not list '{remote_dir}': {exc.user_message}"
                ) from exc
            stack.pop()
            stack.extend(subdirs)
            ctx.flush()
        self._check_quota(ctx, ctx.stats["total_bytes"])
        ctx.stats["planned"] = True
        ctx.stats.pop("plan_stack", None)
        ctx.flush(force=True)
        return None

    def _check_quota(self, ctx, incoming_bytes):
        available = settings.STORAGE_QUOTA_BYTES - FileService.storage_used(ctx.owner)
        if incoming_bytes > available:
            raise JobFailed(
                f"Not enough space: the import needs {filesizeformat(incoming_bytes)} "
                f"but only {filesizeformat(max(available, 0))} are left."
            )

    # -- copying phase -------------------------------------------------

    def _copy(self, ctx, source):
        if "copy_stack" not in ctx.stats:
            root = self._resolve_root(ctx)
            ctx.stats["copy_stack"] = [
                [ctx.options.get("source_path", "/"), str(root.uuid) if root else None]
            ]
        stack = ctx.stats["copy_stack"]
        consecutive_errors = 0
        copied_since_quota_check = 0
        while stack:
            if stop := ctx.should_stop():
                ctx.flush(force=True)
                return stop
            remote_dir, local_parent_id = stack[-1]
            local_parent = self._folder(ctx, local_parent_id)
            try:
                entries = list(source.list_dir(remote_dir))
            except ProviderError as exc:
                ctx.report_item(
                    remote_dir, ImportJobItem.Status.FAILED, error=exc.user_message
                )
                ctx.stat("failed")
                consecutive_errors = self._bump_errors(ctx, consecutive_errors)
                stack.pop()
                continue
            subfolders = []
            for entry in entries:
                if entry.is_dir:
                    folder = self._ensure_folder(ctx, local_parent, entry.name)
                    subfolders.append([entry.id, str(folder.uuid)])
                    continue
                if ctx.already_done(entry.id, entry.fingerprint):
                    continue
                if stop := ctx.should_stop():
                    ctx.flush(force=True)
                    return stop
                ctx.current = entry.id
                try:
                    self._import_file(ctx, source, entry, local_parent)
                except (ProviderError, *_STORAGE_ERRORS) as exc:
                    message = getattr(exc, "user_message", None) or _storage_message(
                        exc
                    )
                    logger.warning(
                        "Import of %s failed: %s",
                        scrub(entry.id[:200]),
                        scrub(str(exc)),
                    )
                    ctx.report_item(
                        entry.id,
                        ImportJobItem.Status.FAILED,
                        error=message,
                        fingerprint=entry.fingerprint,
                    )
                    ctx.stat("failed")
                    consecutive_errors = self._bump_errors(ctx, consecutive_errors)
                    continue
                consecutive_errors = 0
                copied_since_quota_check += 1
                if copied_since_quota_check >= _QUOTA_RECHECK_EVERY:
                    copied_since_quota_check = 0
                    self._check_quota(ctx, 0)
            # This directory's files are done; its subfolders take its place.
            stack.pop()
            stack.extend(subfolders)
            ctx.flush()
        ctx.current = ""
        ctx.stats.pop("copy_stack", None)
        ctx.flush(force=True)
        return Outcome.DONE

    def _bump_errors(self, ctx, consecutive_errors):
        consecutive_errors += 1
        if consecutive_errors >= settings.IMPORTS_MAX_CONSECUTIVE_ERRORS:
            raise JobFailed(
                f"Stopped after {consecutive_errors} consecutive errors - "
                "the remote server looks unavailable."
            )
        return consecutive_errors

    def _folder(self, ctx, folder_id):
        if folder_id is None:
            return None
        folder = (
            FileService.user_files_qs(ctx.owner)
            .filter(uuid=folder_id, node_type=File.NodeType.FOLDER)
            .first()
        )
        if folder is None:
            raise JobFailed("A destination folder disappeared while importing.")
        return folder

    def _resolve_root(self, ctx):
        """The local folder everything lands in. Created once and remembered in
        the stats so a later slice (or a retry) reuses it."""
        destination = None
        if ctx.options.get("destination"):
            destination = (
                FileService.user_files_qs(ctx.owner)
                .filter(uuid=ctx.options["destination"], node_type=File.NodeType.FOLDER)
                .first()
            )
            if destination is None:
                raise JobFailed("The destination folder no longer exists.")
        if not ctx.options.get("create_root_folder", True):
            return destination
        if ctx.stats.get("root_folder"):
            existing = (
                FileService.user_files_qs(ctx.owner)
                .filter(uuid=ctx.stats["root_folder"], node_type=File.NodeType.FOLDER)
                .first()
            )
            if existing is not None:
                return existing
        root = self._ensure_folder(
            ctx, destination, safe_local_name(f"{ctx.connection.label} import")
        )
        ctx.stats["root_folder"] = str(root.uuid)
        ctx.flush(force=True)
        return root

    def _ensure_folder(self, ctx, parent, name):
        """Reuse a same-name folder (folders are not unique per name, but two
        'Documents' side by side after an import is never what anyone wants)."""
        name = safe_local_name(name)
        existing = (
            FileService.user_files_qs(ctx.owner)
            .filter(parent=parent, node_type=File.NodeType.FOLDER, name=name)
            .first()
        )
        if existing is not None:
            return existing
        folder = FileService.create_folder(ctx.owner, name, parent)
        ctx.stat("folders")
        return folder

    def _import_file(self, ctx, source, entry, parent):
        name = safe_local_name(entry.name)
        mime_type = entry.mime_type if len(entry.mime_type) <= _MAX_MIME_LENGTH else ""
        existing = find_name_conflict(ctx.owner, parent, name)
        on_conflict = ctx.options.get("on_conflict", ON_CONFLICT_RENAME)
        if existing is not None and on_conflict == ON_CONFLICT_SKIP:
            ctx.report_item(
                entry.id,
                ImportJobItem.Status.SKIPPED,
                fingerprint=entry.fingerprint,
                target_uuid=existing.uuid,
            )
            ctx.stat("skipped")
            return
        if existing is not None and on_conflict == ON_CONFLICT_RENAME:
            name = available_file_name(ctx.owner, parent, name)
            existing = None

        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_MEMORY) as spool:
            with source.open(entry) as stream:
                shutil.copyfileobj(stream, spool, 1024 * 1024)
            size = spool.tell()
            spool.seek(0)
            content = DjangoFile(spool, name=name)
            content.size = size
            if existing is not None:
                file_obj = FileService.update_content(
                    existing,
                    content,
                    name=name,
                    mime_type=mime_type or None,
                    acting_user=ctx.owner,
                )
            else:
                file_obj = FileService.create_file(
                    ctx.owner,
                    name,
                    parent,
                    content=content,
                    mime_type=mime_type or None,
                )
                if entry.modified_at is not None:
                    # Keep the source's modification date on a brand-new row
                    # (nothing has cached it yet); auto_now only yields to a
                    # queryset update. A replaced file keeps its fresh stamp so
                    # ETags move forward.
                    File.objects.filter(pk=file_obj.pk).update(
                        updated_at=entry.modified_at
                    )
        ctx.report_item(
            entry.id,
            ImportJobItem.Status.DONE,
            target_uuid=file_obj.uuid,
            fingerprint=entry.fingerprint,
        )
        ctx.stat("files")
        ctx.stat("bytes", size)


def _storage_message(exc):
    if isinstance(exc, IntegrityError):
        return "Could not store the file: it was imported concurrently."
    if isinstance(exc, DataError | ValidationError | ValueError):
        return "Could not store the file: the name or type is not accepted."
    return "Could not store the file."
