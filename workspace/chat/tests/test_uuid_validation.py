"""Regression tests: malformed UUID query/header values must not crash with 500.

A non-UUID string passed to ``Model.objects.get(uuid=...)``,
``filter(uuid=...)``, or ``Q(...uuid=...)`` causes Django's
``UUIDField.to_python`` to raise ``ValidationError`` deep in the
cleaning layer. The surrounding ``except Model.DoesNotExist`` does not
catch it, so the exception escapes the view as a bare 500.

These tests pin the fix that routes user-supplied UUIDs through
``parse_uuid_or_none`` at the view boundary.
"""

from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.sse_provider import ChatSSEProvider
from workspace.chat.tests.test_chat import ChatTestMixin


class MessagesBeforeCursorTests(ChatTestMixin, APITestCase):
    """GET /api/v1/chat/conversations/<id>/messages?before=<bad>"""

    def url(self):
        return f"/api/v1/chat/conversations/{self.group.pk}/messages"

    def test_malformed_before_cursor_falls_back_to_no_cursor(self):
        """A non-UUID ?before is treated as 'no cursor', mirroring unknown UUID."""
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.url(), {"before": "not-a-uuid"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("messages", resp.data)


class MessagesUIBeforeCursorTests(ChatTestMixin, APITestCase):
    """GET /chat/<uuid>/messages?before=<bad> (UI partial)"""

    def url(self):
        return f"/chat/{self.group.pk}/messages"

    def test_malformed_before_cursor_falls_back_to_no_cursor(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.url(), {"before": "not-a-uuid"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class SSELastEventIDTests(ChatTestMixin, APITestCase):
    """ChatSSEProvider must not crash on a malformed Last-Event-ID."""

    def test_malformed_last_event_id_does_not_crash(self):
        """The provider treats a non-UUID Last-Event-ID as 'no cursor'."""
        # Constructing the provider directly exercises the code path that
        # parses Last-Event-ID. A 500 here would crash an active SSE stream.
        provider = ChatSSEProvider(self.creator, last_event_id="not-a-uuid")
        # Did not raise: the provider must still have a sensible 'since'
        # timestamp instead of leaking a ValidationError.
        self.assertIsNotNone(provider._since)
