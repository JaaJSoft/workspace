"""Custom file storage that overwrites files instead of creating duplicates."""
from django.core.files.storage import FileSystemStorage


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
        """
        # Delete the file if it already exists
        if self.exists(name):
            try:
                self.delete(name)
                # Force garbage collection on Windows to release file handles
                import gc
                gc.collect()
            except Exception as e:
                # If deletion fails, log but don't fail the operation
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Could not delete existing file '{name}': {e}")

        # Return the name as-is (no random suffix)
        return name
