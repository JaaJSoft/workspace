from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import Conversation
from workspace.notifications.models import Notification

User = get_user_model()

backfill = import_module("workspace.notifications.migrations.0008_backfill_chat_source")


class BackfillChatSourceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.conv = Conversation.objects.create(created_by=self.alice, kind="dm")

    def _notif(self, **kwargs):
        defaults = {"recipient": self.alice, "origin": "chat", "icon": "", "title": "t"}
        defaults.update(kwargs)
        return Notification.objects.create(**defaults)

    def test_unread_chat_row_with_valid_url_gains_fk(self):
        n = self._notif(url=f"/chat/{self.conv.pk}")
        backfill.backfill_chat_conversations(apps, None)
        n.refresh_from_db()
        self.assertEqual(n.conversation_id, self.conv.pk)

    def test_unparseable_url_is_skipped(self):
        n = self._notif(url="/chat/not-a-uuid")
        backfill.backfill_chat_conversations(apps, None)
        n.refresh_from_db()
        self.assertIsNone(n.conversation_id)

    def test_deleted_conversation_is_skipped(self):
        import uuid as uuid_mod

        n = self._notif(url=f"/chat/{uuid_mod.uuid4()}")
        backfill.backfill_chat_conversations(apps, None)
        n.refresh_from_db()
        self.assertIsNone(n.conversation_id)

    def test_read_rows_and_other_origins_untouched(self):
        from django.utils import timezone

        read = self._notif(url=f"/chat/{self.conv.pk}", read_at=timezone.now())
        other = self._notif(origin="files", url=f"/chat/{self.conv.pk}")
        backfill.backfill_chat_conversations(apps, None)
        read.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(read.conversation_id)
        self.assertIsNone(other.conversation_id)
