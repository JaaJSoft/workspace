"""Serving file content from object storage: streamed, or signed and redirected."""

from urllib.parse import parse_qs, urlsplit

import requests
from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.common.tests.s3 import S3StoragesMixin
from workspace.files.models import File, FileScan, FileShareLink

User = get_user_model()

BLOCKING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "block",
}


def _body(response):
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content)
    return response.content


class _ObjectStorageContent(S3StoragesMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="pw")
        self.client.force_authenticate(user=self.user)
        self.payload = bytes(i % 256 for i in range(4096))
        self.file = File(
            owner=self.user, name="clip été.mp4", node_type=File.NodeType.FILE
        )
        self.file.mime_type = "video/mp4"
        self.file.content = ContentFile(self.payload, name="clip été.mp4")
        self.file.size = len(self.payload)
        self.file.save()
        self.content_url = f"/api/v1/files/{self.file.uuid}/content"
        self.download_url = f"/api/v1/files/{self.file.uuid}/download"


class StreamedFromObjectStorageTests(_ObjectStorageContent):
    """Without signed URLs, the app streams the blob out of the bucket."""

    def test_content(self):
        response = self.client.get(self.content_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(_body(response), self.payload)

    def test_a_range(self):
        response = self.client.get(self.content_url, HTTP_RANGE="bytes=1000-1999")

        self.assertEqual(response.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(_body(response), self.payload[1000:2000])

    def test_a_download(self):
        response = self.client.get(self.download_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(_body(response), self.payload)


class SignedDownloadTests(_ObjectStorageContent):
    s3_options = {"signed_urls": True}

    def _redirect(self, response):
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        query = parse_qs(urlsplit(response["Location"]).query)
        return response["Location"], query

    def test_content_redirects_to_the_blob(self):
        location, query = self._redirect(self.client.get(self.content_url))

        self.assertTrue(
            urlsplit(location).path.endswith(
                "/files/users/alice/clip%20%C3%A9t%C3%A9.mp4"
            )
        )
        self.assertEqual(query["response-content-type"], ["video/mp4"])
        self.assertTrue(query["response-content-disposition"][0].startswith("inline;"))
        self.assertIn("X-Amz-Signature", query)
        self.assertEqual(requests.get(location, timeout=10).content, self.payload)

    def test_a_url_names_the_version_on_a_bucket_that_keeps_them(self):
        """A URL naming the key would hand whoever holds it a replacement
        written after it was issued, checked by nobody."""
        try:
            self.s3.put_bucket_versioning(
                Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"}
            )
        except ClientError:
            self.skipTest("This store does not keep versions.")
        blobs = storages["files"]
        blobs.replace(self.file.content.name, ContentFile(self.payload))

        location, query = self._redirect(self.client.get(self.content_url))
        blobs.replace(self.file.content.name, ContentFile(b"replaced"))

        self.assertIn("versionId", query)
        self.assertEqual(requests.get(location, timeout=10).content, self.payload)

    def test_a_range_request_is_redirected_too(self):
        location, _query = self._redirect(
            self.client.get(self.content_url, HTTP_RANGE="bytes=10-19")
        )

        fetched = requests.get(location, headers={"Range": "bytes=10-19"}, timeout=10)
        self.assertEqual(fetched.content, self.payload[10:20])

    def test_a_download_is_an_attachment(self):
        _location, query = self._redirect(self.client.get(self.download_url))

        self.assertTrue(
            query["response-content-disposition"][0].startswith("attachment;")
        )

    def test_a_revalidation_still_gets_its_304(self):
        from workspace.files.views.files import FileViewSet

        response = self.client.get(
            self.content_url, HTTP_IF_NONE_MATCH=FileViewSet._file_etag(self.file)
        )

        self.assertEqual(response.status_code, status.HTTP_304_NOT_MODIFIED)

    def test_no_url_for_someone_without_access(self):
        stranger = User.objects.create_user(username="mallory", password="pw")
        self.client.force_authenticate(user=stranger)

        response = self.client.get(self.content_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(**BLOCKING)
    def test_no_url_for_a_quarantined_file(self):
        FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )

        self.assertEqual(
            self.client.get(self.content_url).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get(self.download_url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_public_link_redirects(self):
        link = FileShareLink.objects.create(file=self.file, created_by=self.user)
        self.client.force_authenticate(user=None)

        _location, query = self._redirect(
            self.client.get(f"/api/v1/files/shared/{link.token}/download")
        )

        self.assertTrue(
            query["response-content-disposition"][0].startswith("attachment;")
        )
