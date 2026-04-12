"""Custom file storage that overwrites files instead of creating duplicates."""
import os

from django.core.files.base import File as DjangoFile
from django.core.files.storage import FileSystemStorage

# Django's default chunk size (64 KB) causes hundreds of small writes for
# multi-MB files.  On replicated storage (e.g. Longhorn) each write must be
# confirmed by every replica, so larger chunks dramatically reduce the number
# of round-trips (224 → 7 for a 14 MB file).
# Configurable via FILE_UPLOAD_CHUNK_SIZE env var (in bytes).
DjangoFile.DEFAULT_CHUNK_SIZE = int(
    os.getenv("FILE_UPLOAD_CHUNK_SIZE", 2 * 1024 * 1024)
)


class OverwriteStorage(FileSystemStorage):
    """
    Custom storage that deletes the old file before saving the new one.

    This prevents Django from appending random strings to filenames
    when a file with the same name already exists.
    """

    def get_available_name(self, name, max_length=None):
        """
        Override to delete the existing file before saving.

        Django's default behavior is to append a random string to the filename
        if a file with that name exists. This override changes that behavior
        to delete the old file and use the exact name requested.

        If the deletion fails (e.g. file locked on Windows), fall back to
        Django's default unique-name generation to avoid an infinite loop
        in FileSystemStorage._save().
        """
        if self.exists(name):
            try:
                self.delete(name)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Could not delete existing file '{name}': {e}")
                # Fall back to Django's default behaviour (unique suffix)
                # so FileSystemStorage._save() does not loop forever.
                return super().get_available_name(name, max_length)

        return name
