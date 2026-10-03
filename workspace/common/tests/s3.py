"""An S3 bucket for tests: moto in-process, or a live server when asked.

Every test gets an empty bucket. ``WORKSPACE_S3_TESTS=1`` with
``WORKSPACE_S3_ENDPOINT_URL`` (and keys) points the same tests at a real
S3-compatible server instead, which is what the live CI job does: moto agrees
with S3 on the API, a real server is where the edge cases live.
"""

import os
import uuid

import boto3
from django.conf import settings
from django.test import override_settings
from moto import mock_aws

from workspace.common.storage.facade import BlobStorage

LIVE = os.environ.get("WORKSPACE_S3_TESTS", "").lower() in {"1", "true", "yes"}


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


class S3TestMixin:
    """Mixed in before the TestCase base; ``self.bucket`` is empty and new."""

    def setUp(self):
        if not LIVE:
            mock = mock_aws()
            mock.start()
            self.addCleanup(mock.stop)
        self.s3_connection = _connection()
        self.bucket = f"workspace-test-{uuid.uuid4().hex[:12]}"
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.s3_connection.get("endpoint_url"),
            region_name=self.s3_connection["region"],
            aws_access_key_id=self.s3_connection["access_key_id"],
            aws_secret_access_key=self.s3_connection["secret_access_key"],
        )
        self.s3.create_bucket(Bucket=self.bucket)
        self.addCleanup(self._drop_bucket)
        super().setUp()

    def _drop_bucket(self):
        if not LIVE:
            return  # moto forgets everything when the mock stops
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", ())]
            if keys:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
        uploads = self.s3.list_multipart_uploads(Bucket=self.bucket).get("Uploads", ())
        for upload in uploads:
            self.s3.abort_multipart_upload(
                Bucket=self.bucket, Key=upload["Key"], UploadId=upload["UploadId"]
            )
        self.s3.delete_bucket(Bucket=self.bucket)

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

    A FileField that resolved its storage when its model was imported keeps
    it; the test points such a field at ``storages[...]`` itself.
    """

    def setUp(self):
        super().setUp()
        options = {"backend": "s3", "bucket": self.bucket, **self.s3_connection}
        blobs = "workspace.common.storage.facade.BlobStorage"
        override = override_settings(
            STORAGES={
                "default": {"BACKEND": blobs, "OPTIONS": options},
                "files": {
                    "BACKEND": blobs,
                    "OPTIONS": {
                        **options,
                        "allow_overwrite": True,
                        "verbatim_names": True,
                    },
                },
                "staticfiles": settings.STORAGES["staticfiles"],
            }
        )
        override.enable()
        self.addCleanup(override.disable)
