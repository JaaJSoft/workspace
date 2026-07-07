from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

User = get_user_model()


class ConversationMediaViewTests(APITestCase):
    """Pins the media panel partition: images tab vs files tab."""

    def setUp(self):
        self.owner = User.objects.create_user(username="mediaowner", password="pass")
        self.outsider = User.objects.create_user(username="mediaout", password="pass")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Media",
            created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.owner)
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.owner,
            body="attachments",
        )
        self.client.force_authenticate(self.owner)

    def url(self, **params):
        base = f"/api/v1/chat/conversations/{self.conv.uuid}/medias"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return f"{base}?{qs}"
        return base

    def _attach(self, name, mime, category):
        return MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(name, b"x", content_type=mime),
            original_name=name,
            mime_type=mime,
            type="unknown",
            category=category,
            size=1,
        )

    def _names(self, resp):
        return {item["original_name"] for item in resp.json()["results"]}

    def test_images_tab_returns_images_and_videos(self):
        self._attach("pic.png", "image/png", "image")
        self._attach("clip.mp4", "video/mp4", "video")
        self._attach("doc.pdf", "application/pdf", "document")
        resp = self.client.get(self.url(type="images"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._names(resp), {"pic.png", "clip.mp4"})
        self.assertEqual(resp.json()["total"], 2)

    def test_files_tab_returns_the_complement(self):
        self._attach("pic.png", "image/png", "image")
        self._attach("doc.pdf", "application/pdf", "document")
        self._attach("data.csv", "text/csv", "text")
        resp = self.client.get(self.url(type="files"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._names(resp), {"doc.pdf", "data.csv"})

    def test_legacy_unknown_category_rows_partition_by_mime(self):
        """Rows created before category detection existed have
        category='unknown' and must still land in the right tab, mirroring
        the is_image/is_video model properties."""
        self._attach("old-pic.png", "image/png", "unknown")
        self._attach("old-clip.mp4", "video/mp4", "unknown")
        self._attach("old-doc.pdf", "application/pdf", "unknown")
        images = self.client.get(self.url(type="images"))
        self.assertEqual(self._names(images), {"old-pic.png", "old-clip.mp4"})
        files = self.client.get(self.url(type="files"))
        self.assertEqual(self._names(files), {"old-doc.pdf"})

    def test_all_returns_everything(self):
        self._attach("pic.png", "image/png", "image")
        self._attach("doc.pdf", "application/pdf", "document")
        resp = self.client.get(self.url(type="all"))
        self.assertEqual(self._names(resp), {"pic.png", "doc.pdf"})

    def test_invalid_type_returns_400(self):
        resp = self.client.get(self.url(type="bogus"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_member_gets_403(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(type="images"))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_pagination_total_and_slice(self):
        for i in range(5):
            self._attach(f"pic{i}.png", "image/png", "image")
        resp = self.client.get(self.url(type="images", offset=0, limit=2))
        body = resp.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(len(body["results"]), 2)
