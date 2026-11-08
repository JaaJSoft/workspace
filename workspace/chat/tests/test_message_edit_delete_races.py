"""An edit or a delete racing a delete of the same message.

Each test lands a complete DELETE between the moment the request under test
loads the message and the moment it writes it, which is the window two
browsers (or a double click) open on the same message.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.query import QuerySet
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.posting import post_message

User = get_user_model()


class MessageRaceTests(APITestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="pass")
        self.reader = User.objects.create_user(username="reader", password="pass")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Team", created_by=self.author
        )
        for user in (self.author, self.reader):
            ConversationMember.objects.create(conversation=self.conv, user=user)
        post_message(self.conv, self.author, "First", body_html="<p>First</p>")
        self.message = post_message(
            self.conv, self.author, "Secret", body_html="<p>Secret</p>"
        )
        self.url = (
            f"/api/v1/chat/conversations/{self.conv.uuid}/messages/{self.message.uuid}"
        )
        self.client.force_authenticate(self.author)

    def tearDown(self):
        cache.clear()

    def _deleted_once_loaded(self):
        """Delete the message right after the request loads it."""
        real_get = QuerySet.get
        deleted = []

        def get(queryset, *args, **kwargs):
            row = real_get(queryset, *args, **kwargs)
            if queryset.model is Message and not deleted:
                deleted.append(True)
                response = self.client.delete(self.url)
                self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            return row

        return patch.object(QuerySet, "get", get)

    def _reader_unread(self):
        return ConversationMember.objects.get(
            conversation=self.conv, user=self.reader
        ).unread_count

    def test_an_edit_does_not_write_over_a_delete(self):
        with self._deleted_once_loaded():
            response = self.client.patch(self.url, {"body": "Edited"}, format="json")

        self.message.refresh_from_db()
        self.assertIsNotNone(self.message.deleted_at)
        self.assertEqual(self.message.body, "")
        self.assertEqual(self.message.body_html, "")
        self.assertIsNone(self.message.edited_at)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_second_delete_does_not_retract_the_unread_twice(self):
        self.assertEqual(self._reader_unread(), 2)

        with self._deleted_once_loaded():
            response = self.client.delete(self.url)

        self.assertEqual(self._reader_unread(), 1)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
