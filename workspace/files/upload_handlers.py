"""Upload handler that hashes a request body while it streams to disk."""

from django.core.files.base import File as DjangoFile
from django.core.files.uploadhandler import TemporaryFileUploadHandler

from workspace.files.services.content_hash import new_hasher


class HashingTemporaryFileUploadHandler(TemporaryFileUploadHandler):
    """Spool to ``FILE_UPLOAD_TEMP_DIR``, hashing the bytes on the way in.

    The digest lands on the upload as ``content_hash``, which spares
    ``FileService`` a second full read of the file. Bytes reach the disk in
    ``DEFAULT_CHUNK_SIZE`` writes rather than the parser's 64 KB chunks, as on
    the WebDAV path: on replicated storage every write is a round trip.
    """

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self._hasher = new_hasher()
        self._pending = []
        self._pending_size = 0

    def receive_data_chunk(self, raw_data, start):
        self._hasher.update(raw_data)
        # Joined once per block: growing a bytearray chunk by chunk copies
        # each byte several times over, at a cost on par with the hashing.
        self._pending.append(raw_data)
        self._pending_size += len(raw_data)
        if self._pending_size >= DjangoFile.DEFAULT_CHUNK_SIZE:
            self._write_pending()

    def file_complete(self, file_size):
        self._write_pending()
        upload = super().file_complete(file_size)
        upload.content_hash = self._hasher.hexdigest()
        return upload

    def _write_pending(self):
        self.file.write(b"".join(self._pending))
        self._pending = []
        self._pending_size = 0
