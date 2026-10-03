"""The one storage class the app holds: Django's Storage API plus the verbs it
lacks.

``FileField`` and ``default_storage`` both resolve to a BlobStorage. It owns
the name policy and hands every byte to a backend (``backend.py``), so the code
that moves a folder or streams an upload never asks where the bytes live.
"""

import errno
import logging
import uuid

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

from workspace.common.logging import scrub

from .backend import NameTaken

logger = logging.getLogger(__name__)

# How many fresh names a save tries when other writers keep taking them.
_SAVE_ATTEMPTS = 10


def _local(**options):
    from .local import LocalBackend

    return LocalBackend(**options)


def _s3(**options):
    from .s3 import S3Backend

    return S3Backend(**options)


_BACKENDS = {"local": _local, "s3": _s3}


@deconstructible(path="workspace.common.storage.facade.BlobStorage")
class BlobStorage(Storage):
    """A Django storage over a blob backend.

    *allow_overwrite* is the name policy, the same as FileSystemStorage's: a
    save under a taken name replaces the blob, where the default picks a free
    name beside it. The remaining options configure the backend.
    """

    def __init__(self, backend="local", *, allow_overwrite=False, **options):
        try:
            build = _BACKENDS[backend]
        except KeyError:
            raise ImproperlyConfigured(
                f"Unknown blob storage backend {backend!r}; "
                f"expected one of {sorted(_BACKENDS)}"
            ) from None
        self.allow_overwrite = allow_overwrite
        self.backend = build(allow_overwrite=allow_overwrite, **options)

    # Name policy.

    def is_name_available(self, name, max_length=None):
        if self.allow_overwrite:
            return not (max_length and len(name) > max_length)
        return super().is_name_available(name, max_length=max_length)

    def get_alternative_name(self, file_root, file_ext):
        if self.allow_overwrite:
            return f"{file_root}{file_ext}"
        return super().get_alternative_name(file_root, file_ext)

    # Django's Storage API, passed through.

    def _open(self, name, mode="rb"):
        return self.backend.open(name, mode)

    def _save(self, name, content):
        for _attempt in range(_SAVE_ATTEMPTS):
            try:
                return self.backend.save(name, content)
            except NameTaken:
                # Another writer took the name since get_available_name said it
                # was free.
                name = self.get_available_name(name)
        raise NameTaken(errno.EEXIST, "No free name found", name)

    def delete(self, name):
        self.backend.delete(name)

    def exists(self, name):
        return self.backend.exists(name)

    def listdir(self, path):
        return self.backend.listdir(path)

    def size(self, name):
        return self.backend.size(name)

    def url(self, name):
        return self.backend.url(name)

    def path(self, name):
        return self.backend.path(name)

    def get_accessed_time(self, name):
        return self.backend.get_accessed_time(name)

    def get_created_time(self, name):
        return self.backend.get_created_time(name)

    def get_modified_time(self, name):
        return self.backend.get_modified_time(name)

    # What Django's API lacks. Contracts on ``Backend``.

    def is_dir(self, name):
        return self.backend.is_dir(name)

    def is_file(self, name):
        return self.backend.is_file(name)

    def scan(self, name):
        return self.backend.scan(name)

    def make_dir(self, name):
        self.backend.make_dir(name)

    def remove_dir_if_empty(self, name):
        return self.backend.remove_dir_if_empty(name)

    def delete_prefix(self, name):
        return self.backend.delete_prefix(name)

    def move(self, source, destination):
        self.backend.move(source, destination)

    def staged_writer(self, name):
        return self.backend.staged_writer(name)

    def local_path(self, name, *, max_bytes):
        return self.backend.local_path(name, max_bytes=max_bytes)

    def replace(self, name, content):
        """Put *content* at *name*, swapping the previous blob in one step.

        Replacing is the point, so the name policy does not apply: the blob
        goes under *name* whatever is there. A backend that overwrites in place
        truncates the blob at the first byte written: a transfer that dies
        halfway would leave neither the old bytes nor the whole new ones, and a
        row rolled back to the version it described before would point at
        content that no longer exists. Saving beside the target and moving
        over it means the name only ever holds one complete version or the
        other; a backend whose writes publish whole needs no staging.

        Returns the name the content was stored under.
        """
        if not hasattr(content, "chunks"):
            content = File(content, name)
        if self.backend.atomic_save:
            writer = self.backend.staged_writer(name)
            try:
                for chunk in content.chunks():
                    writer.write(chunk)
                writer.commit()
            except BaseException:
                writer.abort()
                raise
            return name

        staged = f"{name}.{uuid.uuid4().hex}.part"
        try:
            staged = self.save(staged, content)
            self.backend.move(staged, name, overwrite=True)
        except OSError:
            # Including a save that died partway: the half-written bytes are in
            # the staged blob, and the target still holds the version before.
            try:
                self.delete(staged)
            except OSError:
                logger.warning("Could not remove staged blob %s", scrub(staged))
            raise
        return name
