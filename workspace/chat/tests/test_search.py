from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Message, MessageAttachment

from .test_chat import ChatTestMixin


class ConversationMessageSearchTests(ChatTestMixin, APITestCase):
    """Tests for GET /api/v1/chat/conversations/<id>/messages/search?q=..."""

    def url(self, conv_id):
        return f"/api/v1/chat/conversations/{conv_id}/messages/search"

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url(self.group.uuid), {"q": "hello"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_rejected(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.group.uuid), {"q": "hello"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_query_returns_400(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"q": ""})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_query_returns_400(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_can_search(self):
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="hello world",
        )
        Message.objects.create(
            conversation=self.group,
            author=self.member,
            body="goodbye world",
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"q": "hello"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["query"], "hello")
        self.assertEqual(resp.data["results"][0]["body"], "hello world")

    def test_case_insensitive_search(self):
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="Hello World",
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"q": "hello"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)

    def test_deleted_messages_excluded(self):
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="visible hello",
        )
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="deleted hello",
            deleted_at=timezone.now(),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"q": "hello"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["body"], "visible hello")

    def test_no_results(self):
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="hello world",
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"q": "nonexistent"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["results"], [])

    def test_results_ordered_newest_first(self):
        msg1 = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="first hello",
        )
        msg2 = Message.objects.create(
            conversation=self.group,
            author=self.member,
            body="second hello",
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"q": "hello"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 2)
        # Newest first
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg2.uuid))
        self.assertEqual(resp.data["results"][1]["uuid"], str(msg1.uuid))

    # -- Filter: author --------------------------------------

    def test_filter_by_author(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body="msg by creator"
        )
        Message.objects.create(
            conversation=self.group, author=self.member, body="msg by member"
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"author": self.creator.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["author"]["id"], self.creator.id)

    def test_filter_by_author_combined_with_query(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body="hello from creator"
        )
        Message.objects.create(
            conversation=self.group, author=self.member, body="hello from member"
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body="goodbye from creator"
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(
            self.url(self.group.uuid),
            {
                "q": "hello",
                "author": self.creator.id,
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["body"], "hello from creator")

    # -- Filter: date_range ----------------------------------

    def test_filter_date_range_today(self):
        msg_today = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="today msg",
        )
        msg_old = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="old msg",
        )
        # Push msg_old to 3 days ago
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=3),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"date_range": "today"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_today.uuid))

    def test_filter_date_range_7d(self):
        msg_recent = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="recent msg",
        )
        msg_old = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="old msg",
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=10),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"date_range": "7d"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_recent.uuid))

    def test_filter_date_range_30d(self):
        msg_recent = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="recent msg",
        )
        msg_old = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="old msg",
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=60),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"date_range": "30d"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_recent.uuid))

    # -- Filter: custom date range ---------------------------

    def test_filter_custom_date_from(self):
        msg_recent = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="recent",
        )
        msg_old = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="old",
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=10),
        )

        date_from = (timezone.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"date_from": date_from})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_recent.uuid))

    def test_filter_custom_date_to(self):
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="recent",
        )
        msg_old = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="old",
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=10),
        )

        date_to = (timezone.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"date_to": date_to})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_old.uuid))

    def test_filter_custom_date_range_both(self):
        msg1 = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="msg1",
        )
        msg2 = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="msg2",
        )
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="msg3",
        )
        # msg1 = 20 days ago, msg2 = 5 days ago, msg3 = today
        Message.objects.filter(uuid=msg1.uuid).update(
            created_at=timezone.now() - timedelta(days=20),
        )
        Message.objects.filter(uuid=msg2.uuid).update(
            created_at=timezone.now() - timedelta(days=5),
        )

        date_from = (timezone.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        date_to = (timezone.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        self.client.force_authenticate(self.member)
        resp = self.client.get(
            self.url(self.group.uuid),
            {
                "date_from": date_from,
                "date_to": date_to,
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg2.uuid))

    # -- Filter: has_files / has_images ----------------------

    def _attach(self, msg, mime_type="application/pdf", name="file.pdf"):
        return MessageAttachment.objects.create(
            message=msg,
            file=SimpleUploadedFile(name, b"fake-content", content_type=mime_type),
            original_name=name,
            mime_type=mime_type,
            size=12,
        )

    def test_filter_has_files(self):
        msg_with = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="has attachment",
        )
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="no attachment",
        )
        self._attach(msg_with)

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"has_files": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_with.uuid))

    def test_filter_has_images(self):
        msg_img = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="has image",
        )
        msg_pdf = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="has pdf",
        )
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="no attachment",
        )
        self._attach(msg_img, mime_type="image/png", name="photo.png")
        self._attach(msg_pdf, mime_type="application/pdf", name="doc.pdf")

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"has_images": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_img.uuid))

    def test_filter_has_files_no_duplicates(self):
        """A message with 2 attachments should appear once, not twice."""
        msg = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="multi attach",
        )
        self._attach(msg, name="a.pdf")
        self._attach(msg, name="b.pdf")

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"has_files": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)

    # -- Combined filters ------------------------------------

    def test_combined_author_and_has_files(self):
        msg1 = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="creator with file",
        )
        msg2 = Message.objects.create(
            conversation=self.group,
            author=self.member,
            body="member with file",
        )
        self._attach(msg1)
        self._attach(msg2)

        self.client.force_authenticate(self.member)
        resp = self.client.get(
            self.url(self.group.uuid),
            {
                "author": self.creator.id,
                "has_files": "true",
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg1.uuid))

    def test_combined_query_and_date_range(self):
        msg_today = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="hello today",
        )
        msg_old = Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="hello old",
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=3),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(
            self.url(self.group.uuid),
            {
                "q": "hello",
                "date_range": "today",
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["uuid"], str(msg_today.uuid))

    # -- Validation ------------------------------------------

    def test_no_criteria_returns_400(self):
        """No q, no filters -> 400."""
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_only_no_query_is_ok(self):
        """Filters without q should succeed."""
        Message.objects.create(
            conversation=self.group,
            author=self.creator,
            body="some message",
        )
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {"author": self.creator.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
