"""Files importer: walks a remote file tree and writes it into the files module."""

import logging
import shutil
import tempfile

from django.conf import settings
from django.core.files.base import File as DjangoFile
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

    # -- listing phase -------------------------------------------------

    def _plan(self, ctx, source):
        """Count files and bytes so the UI has a total and the quota can be
        checked before anything is written."""
        total_files = total_bytes = 0
        stack = [ctx.options.get("source_path", "/")]
        while stack:
            if stop := ctx.should_stop():
                return stop
            remote_dir = stack.pop()
            try:
                for entry in source.list_dir(remote_dir):
                    if entry.is_dir:
                        stack.append(entry.id)
                    else:
                        total_files += 1
                        total_bytes += entry.size or 0
            except ProviderError as exc:
                raise JobFailed(
                    f"Could not list '{remote_dir}': {exc.user_message}"
                ) from exc
            ctx.stats["total_files"] = total_files
            ctx.stats["total_bytes"] = total_bytes
            ctx.flush()
        self._check_quota(ctx, total_bytes)
        ctx.stats["planned"] = True
        ctx.flush(force=True)
        return None

    def _check_quota(self, ctx, incoming_bytes):
        available = settings.STORAGE_QUOTA_BYTES - FileService.storage_used(ctx.owner)
        if incoming_bytes > available:
            raise JobFailed(
                f"Not enough space: the import needs {_human(incoming_bytes)} "
                f"but only {_human(max(available, 0))} are left."
            )

    # -- copying phase -------------------------------------------------

    def _copy(self, ctx, source):
        root = self._resolve_root(ctx)
        stack = [(ctx.options.get("source_path", "/"), root)]
        consecutive_errors = 0
        copied_since_quota_check = 0
        while stack:
            if stop := ctx.should_stop():
                return stop
            remote_dir, local_parent = stack.pop()
            try:
                entries = list(source.list_dir(remote_dir))
            except ProviderError as exc:
                ctx.report_item(
                    remote_dir, ImportJobItem.Status.FAILED, error=exc.user_message
                )
                ctx.stat("failed")
                consecutive_errors = self._bump_errors(ctx, consecutive_errors)
                continue
            for entry in entries:
                if entry.is_dir:
                    stack.append(
                        (entry.id, self._ensure_folder(ctx, local_parent, entry.name))
                    )
                    continue
                if ctx.already_done(entry.id, entry.etag):
                    continue
                if stop := ctx.should_stop():
                    return stop
                ctx.current = entry.id
                try:
                    self._import_file(ctx, source, entry, local_parent)
                except (ProviderError, OSError) as exc:
                    message = getattr(exc, "user_message", "Could not store the file.")
                    logger.warning(
                        "Import of %s failed: %s",
                        scrub(entry.id[:200]),
                        scrub(str(exc)),
                    )
                    ctx.report_item(
                        entry.id,
                        ImportJobItem.Status.FAILED,
                        error=message,
                        etag=entry.etag,
                    )
                    ctx.stat("failed")
                    consecutive_errors = self._bump_errors(ctx, consecutive_errors)
                    continue
                consecutive_errors = 0
                copied_since_quota_check += 1
                if copied_since_quota_check >= _QUOTA_RECHECK_EVERY:
                    copied_since_quota_check = 0
                    self._check_quota(ctx, 0)
        ctx.current = ""
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
        root = self._ensure_folder(ctx, destination, f"{ctx.connection.label} import")
        ctx.stats["root_folder"] = str(root.uuid)
        ctx.flush(force=True)
        return root

    def _ensure_folder(self, ctx, parent, name):
        """Reuse a same-name folder (folders are not unique per name, but two
        'Documents' side by side after an import is never what anyone wants)."""
        existing = (
            FileService.user_files_qs(ctx.owner)
            .filter(parent=parent, node_type=File.NodeType.FOLDER, name__iexact=name)
            .first()
        )
        if existing is not None:
            return existing
        folder = FileService.create_folder(ctx.owner, name, parent)
        ctx.stat("folders")
        return folder

    def _import_file(self, ctx, source, entry, parent):
        name = entry.name
        existing = find_name_conflict(ctx.owner, parent, name)
        on_conflict = ctx.options.get("on_conflict", ON_CONFLICT_RENAME)
        if existing is not None and on_conflict == ON_CONFLICT_SKIP:
            ctx.report_item(
                entry.id,
                ImportJobItem.Status.SKIPPED,
                etag=entry.etag,
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
                    mime_type=entry.mime_type or None,
                    acting_user=ctx.owner,
                )
            else:
                file_obj = FileService.create_file(
                    ctx.owner,
                    name,
                    parent,
                    content=content,
                    mime_type=entry.mime_type or None,
                )
        if entry.modified_at is not None:
            # auto_now only yields to a queryset update.
            File.objects.filter(pk=file_obj.pk).update(updated_at=entry.modified_at)
        ctx.report_item(
            entry.id,
            ImportJobItem.Status.DONE,
            target_uuid=file_obj.uuid,
            etag=entry.etag,
        )
        ctx.stat("files")
        ctx.stat("bytes", size)


def _human(num_bytes):
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
