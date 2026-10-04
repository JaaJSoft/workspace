"""The S3-compatible object storage backend, on boto3.

A storage name maps to the key ``<prefix><name>``, so the bucket mirrors the
layout a disk would hold. A directory is a key prefix: it exists while a key
lives under it, and ``make_dir`` keeps an empty one with a zero-byte marker
``<name>/``. A move is a server-side copy then a delete, so it costs requests,
not bytes.

Reads stream: ``open`` returns a reader that fetches a window of the object at
a time with ranged GETs, so a seek (a video scrubbed, a zip's central directory
read first) never downloads what precedes it. Writes publish whole or not at
all: a single PUT, or a multipart upload completed on commit and aborted
otherwise.
"""

import errno
import io
import logging
import os
import threading
from contextlib import contextmanager

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.core.files.base import File
from django.utils.http import content_disposition_header

from workspace.common.logging import scrub

from .backend import (
    Backend,
    Blob,
    Entry,
    NameTaken,
    Relocation,
    StagedWriter,
    checked_name,
)

logger = logging.getLogger(__name__)

MIN_PART_SIZE = 5 * 1024 * 1024  # S3's floor for every part but the last
MAX_PARTS = 10_000
MAX_KEY_BYTES = 1024
# CopyObject copies up to 5 GiB in one request; past it the copy goes in parts.
MAX_SINGLE_COPY = 5 * 1024**3
DELETE_BATCH = 1000  # DeleteObjects' limit per request

_clients = {}
_clients_lock = threading.Lock()


def _client(**options):
    """The process's S3 client for *options*.

    A low-level boto3 client is thread-safe, so one serves every thread and
    greenlet of the process and keeps their connections pooled. Building one
    costs tens of milliseconds of CPU, which a client per request would pay
    every time. Keyed by PID as well: a client built before a fork would
    share its sockets with the child.
    """
    key = (os.getpid(), *sorted(options.items()))
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            client = _build_client(**options)
            _clients[key] = client
        return client


def _build_client(
    *,
    endpoint_url,
    region,
    access_key_id,
    secret_access_key,
    addressing_style,
    max_pool_connections,
):
    session = boto3.session.Session(
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region,
    )
    return session.client(
        "s3",
        endpoint_url=endpoint_url,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": addressing_style or "auto"},
            retries={"mode": "standard", "max_attempts": 5},
            max_pool_connections=max_pool_connections,
            # Checksums on every request are newer than most S3-compatible
            # servers; send them only where the API requires one.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def _status(exc):
    return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def _code(exc):
    return exc.response.get("Error", {}).get("Code", "")


@contextmanager
def _translated(name):
    """Raise what a filesystem would for a failed request on *name*."""
    try:
        yield
    except ClientError as exc:
        status, code = _status(exc), _code(exc)
        if status == 404 or code in ("NoSuchKey", "NotFound", "404"):
            raise FileNotFoundError(errno.ENOENT, "No such blob", name) from exc
        if status == 412 or code == "PreconditionFailed":
            raise NameTaken(errno.EEXIST, "The name is taken", name) from exc
        if status == 403:
            raise PermissionError(
                errno.EACCES, f"Access denied ({code})", name
            ) from exc
        raise OSError(
            errno.EIO, f"Object storage refused the request ({code})", name
        ) from exc
    except BotoCoreError as exc:
        raise OSError(errno.EIO, f"Object storage request failed: {exc}", name) from exc


class S3Backend(Backend):
    def __init__(
        self,
        *,
        bucket,
        allow_overwrite=False,
        prefix="",
        endpoint_url=None,
        region=None,
        access_key_id=None,
        secret_access_key=None,
        addressing_style=None,
        presign_endpoint_url=None,
        url_ttl=300,
        signed_urls=False,
        signed_url_ttl=3600,
        conditional_writes=True,
        part_size=8 * 1024 * 1024,
        read_window=8 * 1024 * 1024,
        max_pool_connections=50,
    ):
        if part_size < MIN_PART_SIZE:
            raise ValueError(f"part_size must be at least {MIN_PART_SIZE} bytes")
        self.bucket = bucket
        self.prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        self.url_ttl = url_ttl
        # Serving through signed URLs is opt-in: a browser fetch() that follows
        # the redirect needs the bucket to answer CORS for the app's origin.
        self.signed_urls = signed_urls
        # Long enough for a video to play to its end: a player can keep
        # fetching ranges from the URL it was redirected to.
        self.signed_url_ttl = signed_url_ttl
        self.part_size = part_size
        self.read_window = read_window
        # A save under a taken name must not replace the blob there. A
        # conditional write makes the check and the write one request; without
        # it, the name was only checked free (a HEAD) before the write.
        self.exclusive_saves = not allow_overwrite and conditional_writes
        self._connection = {
            "endpoint_url": endpoint_url,
            "region": region,
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "addressing_style": addressing_style,
            "max_pool_connections": max_pool_connections,
        }
        # The host a client fetches a signed URL from is part of what is
        # signed, so a server reached under another name (an internal service
        # name, a public reverse proxy) needs URLs signed for the public one.
        self._presign_connection = {
            **self._connection,
            "endpoint_url": presign_endpoint_url or endpoint_url,
        }

    @property
    def client(self):
        return _client(**self._connection)

    def _key(self, name):
        key = f"{self.prefix}{checked_name(name)}"
        if len(key.encode()) > MAX_KEY_BYTES:
            raise OSError(errno.ENAMETOOLONG, "Name too long for object storage", name)
        return key

    def _dir_prefix(self, name):
        return self._key(name) + "/"

    def _head(self, key, name):
        """The object's metadata, or None when there is no object at *key*."""
        try:
            with _translated(name):
                return self.client.head_object(Bucket=self.bucket, Key=key)
        except FileNotFoundError:
            return None

    def _pages(self, prefix, name, **params):
        paginator = self.client.get_paginator("list_objects_v2")
        with _translated(name):
            yield from paginator.paginate(Bucket=self.bucket, Prefix=prefix, **params)

    def _objects(self, prefix, name):
        for page in self._pages(prefix, name):
            yield from page.get("Contents", ())

    def _delete_keys(self, keys, name):
        """Delete *keys* in batches; returns the keys that could not be deleted."""
        failed = []
        for start in range(0, len(keys), DELETE_BATCH):
            batch = keys[start : start + DELETE_BATCH]
            with _translated(name):
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
            failed.extend(error["Key"] for error in response.get("Errors", ()))
        return failed

    def _copy(self, source_key, destination_key, size, name):
        with _translated(name):
            if size <= MAX_SINGLE_COPY:
                self.client.copy_object(
                    Bucket=self.bucket,
                    Key=destination_key,
                    CopySource={"Bucket": self.bucket, "Key": source_key},
                )
            else:
                self.client.copy(
                    {"Bucket": self.bucket, "Key": source_key},
                    self.bucket,
                    destination_key,
                )

    # Django's Storage API.

    def open(self, name, mode="rb"):
        if any(flag in mode for flag in "wax+"):
            raise ValueError(
                "Object storage blobs are written with save() or staged_writer()"
            )
        key = self._key(name)
        with _translated(name):
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        size = head["ContentLength"]
        reader = io.BufferedReader(
            _RangeReader(self, key, size, name), buffer_size=256 * 1024
        )
        handle = File(
            reader if "b" in mode else io.TextIOWrapper(reader, encoding="utf-8"),
            name=name,
        )
        handle.size = size
        return handle

    def save(self, name, content):
        upload = _Upload(self, self._key(name), name, exclusive=self.exclusive_saves)
        try:
            for chunk in content.chunks():
                upload.write(chunk)
            upload.commit()
        except BaseException:
            upload.abort()
            raise
        return name

    def delete(self, name):
        with _translated(name):
            self.client.delete_object(Bucket=self.bucket, Key=self._key(name))

    def exists(self, name):
        if self._head(self._key(name), name) is not None:
            return True
        return self.is_dir(name)

    def listdir(self, name):
        prefix = self._dir_prefix(name) if name not in ("", ".") else self.prefix
        directories, files, found = [], [], False
        for page in self._pages(prefix, name, Delimiter="/"):
            for common in page.get("CommonPrefixes", ()):
                found = True
                directories.append(common["Prefix"][len(prefix) : -1])
            for obj in page.get("Contents", ()):
                found = True
                if obj["Key"] != prefix:
                    files.append(obj["Key"][len(prefix) :])
        if not found and prefix:
            raise FileNotFoundError(errno.ENOENT, "No such directory", name)
        return directories, files

    def size(self, name):
        head = self._head(self._key(name), name)
        if head is None:
            raise FileNotFoundError(errno.ENOENT, "No such blob", name)
        return head["ContentLength"]

    def url(self, name):
        return _client(**self._presign_connection).generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._key(name)},
            ExpiresIn=self.url_ttl,
        )

    def signed_url(self, name, *, filename, attachment, content_type):
        if not self.signed_urls:
            return None
        key = self._key(name)
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ResponseContentType": content_type,
            "ResponseContentDisposition": content_disposition_header(
                attachment, filename
            ),
        }
        # A URL naming the key serves whatever is written there until it
        # expires. A bucket that keeps versions lets it name the version read
        # now, which is the one the caller just checked.
        try:
            head = self._head(key, name)
        except OSError:
            return None
        version = (head or {}).get("VersionId")
        if version and version != "null":
            params["VersionId"] = version
        return _client(**self._presign_connection).generate_presigned_url(
            "get_object", Params=params, ExpiresIn=self.signed_url_ttl
        )

    def get_modified_time(self, name):
        head = self._head(self._key(name), name)
        if head is None:
            raise FileNotFoundError(errno.ENOENT, "No such blob", name)
        return head["LastModified"]

    # What Django's API lacks.

    def is_dir(self, name):
        for page in self._pages(self._dir_prefix(name), name, MaxKeys=1):
            return bool(page.get("Contents"))
        return False

    def is_file(self, name):
        return self._head(self._key(name), name) is not None

    def scan(self, name):
        prefix = self._dir_prefix(name)
        entries, found = [], False
        for page in self._pages(prefix, name, Delimiter="/"):
            for common in page.get("CommonPrefixes", ()):
                found = True
                entries.append(
                    Entry(
                        common["Prefix"][len(prefix) : -1], is_dir=True, is_file=False
                    )
                )
            for obj in page.get("Contents", ()):
                found = True
                if obj["Key"] != prefix:
                    entries.append(
                        Entry(obj["Key"][len(prefix) :], is_dir=False, is_file=True)
                    )
        if not found:
            raise FileNotFoundError(errno.ENOENT, "No such directory", name)
        return entries

    def _name(self, key):
        return key[len(self.prefix) :]

    def iter_blobs(self, name):
        for obj in self._objects(self._dir_prefix(name), name):
            if not obj["Key"].endswith("/"):
                yield Blob(
                    name=self._name(obj["Key"]),
                    size=obj["Size"],
                    modified=obj["LastModified"],
                )

    def iter_dirs(self, name):
        # A directory is any prefix a key sits under, or a kept empty one.
        root = self._dir_prefix(name)
        seen = set()
        for obj in self._objects(root, name):
            parts = obj["Key"][len(root) :].split("/")
            for depth in range(1, len(parts)):
                relative = "/".join(parts[:depth])
                if relative and relative not in seen:
                    seen.add(relative)
                    yield f"{name}/{relative}"

    def _put_marker(self, key, name):
        with _translated(name):
            self.client.put_object(Bucket=self.bucket, Key=key, Body=b"")

    def make_dir(self, name):
        self._put_marker(self._dir_prefix(name), name)

    def remove_dir_if_empty(self, name):
        marker = self._dir_prefix(name)
        for page in self._pages(marker, name, MaxKeys=2):
            if [obj["Key"] for obj in page.get("Contents", ())] != [marker]:
                return False
            with _translated(name):
                self.client.delete_object(Bucket=self.bucket, Key=marker)
            return True
        return False

    def delete_prefix(self, name):
        prefix = self._dir_prefix(name)
        batch, deleted, failed = [], False, []
        for obj in self._objects(prefix, name):
            batch.append(obj["Key"])
            if len(batch) == DELETE_BATCH:
                failed += self._delete_keys(batch, name)
                batch, deleted = [], True
        if batch:
            failed += self._delete_keys(batch, name)
            deleted = True
        if failed:
            raise OSError(errno.EIO, f"Could not delete {len(failed)} object(s)", name)
        return deleted

    def relocate(self, source, destination):
        """Copy every key server-side; the sources go on commit().

        A failed copy raises after removing the copies already made, so the
        source stays the one place the data is. Dropping the sources is left
        to the returned relocation, where a failed delete only leaves
        duplicates behind and is logged rather than raised.
        """
        source_key = self._key(source)
        destination_key = self._key(destination)
        head = self._head(source_key, source)
        prefix = None
        sources, copies = {}, []
        try:
            if head is not None:
                self._copy(source_key, destination_key, head["ContentLength"], source)
                sources[source_key] = _generation(head)
                copies.append(destination_key)
            else:
                prefix = source_key + "/"
                if (destination_key + "/").startswith(prefix):
                    raise OSError(
                        errno.EINVAL, "Cannot move a directory into itself", source
                    )
                for obj in self._objects(prefix, source):
                    target = destination_key + "/" + obj["Key"][len(prefix) :]
                    if obj["Key"].endswith("/"):
                        # A marker holds nothing to copy, and some stores
                        # refuse CopyObject on a key that names a directory.
                        self._put_marker(target, source)
                    else:
                        self._copy(obj["Key"], target, obj["Size"], source)
                    sources[obj["Key"]] = _generation(obj)
                    copies.append(target)
                if not sources:
                    raise FileNotFoundError(
                        errno.ENOENT, "No such blob or directory", source
                    )
        except BaseException:
            self._delete_quietly(copies, destination)
            raise
        return _DropSources(self, sources, source, prefix)

    def _generations(self, keys, prefix, name):
        """What each of *keys* holds now, as :func:`_generation` reads it."""
        if prefix is None:
            (key,) = keys
            head = self._head(key, name)
            return {} if head is None else {key: _generation(head)}
        return {obj["Key"]: _generation(obj) for obj in self._objects(prefix, name)}

    def move(self, source, destination, *, overwrite=False):
        self.relocate(source, destination).commit()

    def _delete_quietly(self, keys, name):
        """Delete *keys* as far as possible; returns the keys that remain.

        What remains is logged rather than raised.
        """
        if not keys:
            return []
        try:
            left = self._delete_keys(keys, name)
        except OSError:
            left = keys
        if left:
            logger.warning(
                "Could not delete %d object(s) left behind by a move of %s",
                len(left),
                scrub(name),
            )
        return left

    def staged_writer(self, name):
        return _Upload(self, self._key(name), name, exclusive=False)

    def purge_staged(self, before):
        # Only a multipart upload holds anything before its commit: a write
        # under a part is buffered in memory and dies with its process.
        paginator = self.client.get_paginator("list_multipart_uploads")
        params = {"Prefix": self.prefix} if self.prefix else {}
        with _translated(self.prefix):
            stale = [
                upload
                for page in paginator.paginate(Bucket=self.bucket, **params)
                for upload in page.get("Uploads", ())
                if upload["Initiated"] < before
            ]
        purged = 0
        for upload in stale:
            try:
                with _translated(upload["Key"]):
                    self.client.abort_multipart_upload(
                        Bucket=self.bucket,
                        Key=upload["Key"],
                        UploadId=upload["UploadId"],
                    )
            except FileNotFoundError:
                continue  # completed or aborted since it was listed
            purged += 1
        return purged


def _generation(meta):
    """Which write an object holds, from its HEAD or its listing entry.

    The ETag alone repeats when the same bytes are written again; the time
    tells such a rewrite apart, to the precision the store keeps.
    """
    return meta.get("ETag"), meta.get("LastModified")


class _DropSources(Relocation):
    """Deletes the sources a relocation copied, as they were when it copied them.

    A source path is free once the move commits, so a new file can be written
    there before the delete reaches the store: that object holds the new
    file's bytes, not the ones the move copied, and stays.
    """

    def __init__(self, backend, copied, name, prefix):
        self._backend = backend
        self._copied = copied
        self._name = name
        self._prefix = prefix

    def commit(self):
        copied, self._copied = self._copied, {}
        if not copied:
            return True
        try:
            current = self._backend._generations(copied, self._prefix, self._name)
        except OSError as exc:
            logger.warning(
                "Left the %d source(s) of a move of %s in place: %s",
                len(copied),
                scrub(self._name),
                scrub(exc),
            )
            self._copied = copied  # a second commit tries again
            return False
        unchanged = [key for key, seen in copied.items() if current.get(key) == seen]
        rewritten = sum(
            1 for key, seen in copied.items() if current.get(key, seen) != seen
        )
        if rewritten:
            logger.warning(
                "Kept %d object(s) rewritten after a move of %s copied them",
                rewritten,
                scrub(self._name),
            )
        # A rewritten source holds another write's bytes: keeping it leaves
        # nothing of this move behind.
        return not self._backend._delete_quietly(unchanged, self._name)


class _RangeReader(io.RawIOBase):
    """A seekable reader over one object, a window of ranged GETs at a time.

    A window is read to its end before the next is requested, so its
    connection goes back to the pool; a seek elsewhere drops it. Nothing
    before the position is ever fetched.
    """

    def __init__(self, backend, key, size, name):
        self._backend = backend
        self._key = key
        self._size = size
        self.name = name
        self._pos = 0
        self._body = None
        self._window_end = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._pos + offset
        elif whence == io.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError(f"invalid whence ({whence})")
        if position < 0:
            # What a file answers; zipfile probes a short archive with a seek
            # back from its end and reads OSError as "not a zip".
            raise OSError(errno.EINVAL, "Invalid argument")
        if position != self._pos:
            self._drop_window()
            self._pos = position
        return position

    def readinto(self, buffer):
        if self._pos >= self._size or not len(buffer):
            return 0
        wanted = len(buffer)
        for attempt in (1, 2):
            if self._body is None:
                self._open_window()
            try:
                chunk = self._body.read(min(wanted, self._window_end - self._pos))
            except (BotoCoreError, OSError) as exc:
                # A connection that died mid-stream: pick up from the same byte
                # once, then give up.
                self._drop_window()
                if attempt == 2:
                    raise OSError(errno.EIO, f"Read failed: {exc}", self.name) from exc
                continue
            if not chunk:
                self._drop_window()
                if attempt == 2:
                    raise OSError(errno.EIO, "The object ended early", self.name)
                continue
            n = len(chunk)
            buffer[:n] = chunk
            self._pos += n
            if self._pos >= self._window_end:
                self._drop_window()
            return n
        return 0  # pragma: no cover - both attempts either return or raise

    def _open_window(self):
        end = min(self._pos + self._backend.read_window, self._size)
        with _translated(self.name):
            response = self._backend.client.get_object(
                Bucket=self._backend.bucket,
                Key=self._key,
                Range=f"bytes={self._pos}-{end - 1}",
            )
        self._body = response["Body"]
        self._window_end = end

    def _drop_window(self):
        if self._body is not None:
            self._body.close()
            self._body = None

    def close(self):
        self._drop_window()
        super().close()


class _Upload(StagedWriter):
    """One PUT for a blob under a part, a multipart upload above it.

    Nothing is visible under the key before commit(), and abort() discards an
    unfinished multipart upload instead of completing it, so an interrupted
    write never replaces the previous blob. Parts double in size every
    thousand so that the 10,000 S3 allows cover any object it can hold.
    """

    def __init__(self, backend, key, name, *, exclusive):
        self._backend = backend
        self._key = key
        self._name = name
        self._condition = {"IfNoneMatch": "*"} if exclusive else {}
        self._part_size = backend.part_size
        self._buffer = bytearray()
        self._upload_id = None
        self._parts = []

    def write(self, data):
        self._buffer += data
        if len(self._buffer) >= self._part_size:
            self._upload_part()

    def _upload_part(self):
        client = self._backend.client
        with _translated(self._name):
            if self._upload_id is None:
                self._upload_id = client.create_multipart_upload(
                    Bucket=self._backend.bucket, Key=self._key
                )["UploadId"]
            number = len(self._parts) + 1
            if number > MAX_PARTS:
                raise OSError(errno.EFBIG, "Too large for object storage", self._name)
            response = client.upload_part(
                Bucket=self._backend.bucket,
                Key=self._key,
                UploadId=self._upload_id,
                PartNumber=number,
                Body=bytes(self._buffer),
            )
        self._parts.append({"ETag": response["ETag"], "PartNumber": number})
        self._buffer = bytearray()
        if number % 1000 == 0:
            self._part_size *= 2

    def commit(self):
        client = self._backend.client
        with _translated(self._name):
            if self._upload_id is None:
                client.put_object(
                    Bucket=self._backend.bucket,
                    Key=self._key,
                    Body=bytes(self._buffer),
                    **self._condition,
                )
            else:
                if self._buffer:
                    self._upload_part()
                client.complete_multipart_upload(
                    Bucket=self._backend.bucket,
                    Key=self._key,
                    UploadId=self._upload_id,
                    MultipartUpload={"Parts": self._parts},
                    **self._condition,
                )
                self._upload_id = None
        self._buffer = bytearray()

    def abort(self):
        self._buffer = bytearray()
        if self._upload_id is None:
            return
        upload_id, self._upload_id = self._upload_id, None
        try:
            with _translated(self._name):
                self._backend.client.abort_multipart_upload(
                    Bucket=self._backend.bucket, Key=self._key, UploadId=upload_id
                )
        except OSError as exc:
            # The bucket's lifecycle rule for incomplete uploads collects it.
            logger.warning(
                "Could not abort the upload of %s: %s", scrub(self._name), scrub(exc)
            )
