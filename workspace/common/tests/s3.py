"""An S3 bucket for tests: moto in-process, or a live server when asked.

Every test gets an empty bucket. ``WORKSPACE_S3_TESTS=1`` with
``WORKSPACE_S3_ENDPOINT_URL`` (and keys) points the same tests at a real
S3-compatible server instead, which is what the live CI job does: moto agrees
with S3 on the API, a real server is where the edge cases live.

``WORKSPACE_TEST_STORAGE=s3`` goes further and runs the whole suite with
blobs on that server: the test runner gives the run a bucket of its own
(``use_session_bucket``), and a test that reads a tree back gets an empty
prefix of it (``workspace.common.tests.media``).
"""

import contextlib
import os
import uuid

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.test import override_settings
from moto import mock_aws

from workspace.common.storage.facade import BlobStorage

# The suite on object storage: every blob, not only these tests', in a bucket.
ON_OBJECT_STORAGE = os.environ.get("WORKSPACE_TEST_STORAGE", "").lower() == "s3"

# moto patches every boto3 client of the process, the app's own included: a
# suite on a live server cannot have it intercept what the app writes.
LIVE = ON_OBJECT_STORAGE or os.environ.get("WORKSPACE_S3_TESTS", "").lower() in {
    "1",
    "true",
    "yes",
}

# The bucket the runner made for a suite on object storage.
SESSION_BUCKET_ENV = "WORKSPACE_TEST_S3_BUCKET"


def _connection():
    if LIVE:
        return {
            "endpoint_url": os.environ["WORKSPACE_S3_ENDPOINT_URL"],
            "region": os.environ.get("WORKSPACE_S3_REGION", "us-east-1"),
            "access_key_id": os.environ["WORKSPACE_S3_ACCESS_KEY_ID"],
            "secret_access_key": os.environ["WORKSPACE_S3_SECRET_ACCESS_KEY"],
            "addressing_style": "path",
        }
    return {
        "region": "us-east-1",
        "access_key_id": "testing",
        "secret_access_key": "testing",
    }


def _client(connection):
    return boto3.client(
        "s3",
        endpoint_url=connection.get("endpoint_url"),
        region_name=connection["region"],
        aws_access_key_id=connection["access_key_id"],
        aws_secret_access_key=connection["secret_access_key"],
    )


def s3_storages(bucket, connection, **options):
    """``STORAGES`` with both aliases on *bucket*, as the settings build them."""
    base = {"backend": "s3", "bucket": bucket, **connection, **options}
    blobs = "workspace.common.storage.facade.BlobStorage"
    return {
        "default": {"BACKEND": blobs, "OPTIONS": base},
        "files": {
            "BACKEND": blobs,
            "OPTIONS": {**base, "allow_overwrite": True, "verbatim_names": True},
        },
        "staticfiles": settings.STORAGES["staticfiles"],
    }


def drop_bucket(client, bucket):
    """Empty *bucket* - objects, unfinished uploads, kept versions - and delete it."""
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", ())]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
    for upload in client.list_multipart_uploads(Bucket=bucket).get("Uploads", ()):
        client.abort_multipart_upload(
            Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
        )
    # A test that turned versioning on leaves older versions behind, and a
    # bucket holding any of them cannot be deleted.
    versions = client.get_paginator("list_object_versions")
    with contextlib.suppress(ClientError):
        for page in versions.paginate(Bucket=bucket):
            kept = [*page.get("Versions", ()), *page.get("DeleteMarkers", ())]
            if kept:
                client.delete_objects(
                    Bucket=bucket,
                    Delete={
                        "Objects": [
                            {"Key": v["Key"], "VersionId": v["VersionId"]} for v in kept
                        ]
                    },
                )
    client.delete_bucket(Bucket=bucket)


def use_session_bucket():
    """Put every blob of the run in a new bucket; returns what undoes it.

    For the runner of a suite on object storage. The bucket's name goes in the
    environment, where --parallel workers and isolated tests find it.
    """
    connection = _connection()
    client = _client(connection)
    bucket = f"workspace-suite-{uuid.uuid4().hex[:12]}"
    client.create_bucket(Bucket=bucket)
    os.environ[SESSION_BUCKET_ENV] = bucket
    override = override_settings(STORAGES=s3_storages(bucket, connection))
    override.enable()

    def undo():
        override.disable()
        os.environ.pop(SESSION_BUCKET_ENV, None)
        drop_bucket(client, bucket)

    return undo


def session_storages(prefix):
    """``STORAGES`` on the run's bucket, under *prefix*."""
    return s3_storages(os.environ[SESSION_BUCKET_ENV], _connection(), prefix=prefix)


class S3TestMixin:
    """Mixed in before the TestCase base; ``self.bucket`` is empty and new."""

    def setUp(self):
        if not LIVE:
            mock = mock_aws()
            mock.start()
            self.addCleanup(mock.stop)
        self.s3_connection = _connection()
        self.bucket = f"workspace-test-{uuid.uuid4().hex[:12]}"
        self.s3 = _client(self.s3_connection)
        self.s3.create_bucket(Bucket=self.bucket)
        self.addCleanup(self._drop_bucket)
        super().setUp()

    def _drop_bucket(self):
        if LIVE:  # moto forgets everything when the mock stops
            drop_bucket(self.s3, self.bucket)

    def make_s3_storage(self, **options):
        return BlobStorage(
            backend="s3", bucket=self.bucket, **self.s3_connection, **options
        )

    def keys(self):
        """Every key in the bucket, sorted."""
        paginator = self.s3.get_paginator("list_objects_v2")
        return sorted(
            obj["Key"]
            for page in paginator.paginate(Bucket=self.bucket)
            for obj in page.get("Contents", ())
        )


class S3StoragesMixin(S3TestMixin):
    """Both STORAGES aliases (``default_storage`` included) on the test bucket.

    A FileField whose storage was resolved when its model was imported keeps
    it; File.content looks its storage up when used, so it follows.
    """

    # Extra backend options for both aliases, e.g. {"signed_urls": True}.
    s3_options = {}

    def setUp(self):
        super().setUp()
        override = override_settings(
            STORAGES=s3_storages(self.bucket, self.s3_connection, **self.s3_options)
        )
        override.enable()
        self.addCleanup(override.disable)
