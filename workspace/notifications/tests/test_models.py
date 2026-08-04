from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.chat.models import Conversation
from workspace.files.models import File
from workspace.notifications.models import Notification

User = get_user_model()


class NotificationSourceFieldsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")

    def test_source_fks_default_to_null(self):
        notif = Notification.objects.create(
            recipient=self.alice, origin="mail", icon="", title="Sourceless"
        )
        self.assertIsNone(notif.conversation_id)
        self.assertIsNone(notif.file_id)
        self.assertIsNone(notif.task_id)
        self.assertIsNone(notif.event_id)
        self.assertIsNone(notif.poll_id)

    def test_single_source_fk_accepted(self):
        conv = Conversation.objects.create(created_by=self.alice, kind="dm")
        notif = Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="",
            title="Hi",
            conversation=conv,
        )
        self.assertEqual(notif.conversation_id, conv.pk)

    def test_two_source_fks_rejected(self):
        conv = Conversation.objects.create(created_by=self.alice, kind="dm")
        file_obj = File.objects.create(owner=self.alice, name="a.txt", node_type="file")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(
                recipient=self.alice,
                origin="chat",
                icon="",
                title="Bad",
                conversation=conv,
                file=file_obj,
            )

    def test_deleting_source_cascades(self):
        conv = Conversation.objects.create(created_by=self.alice, kind="dm")
        Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="",
            title="Hi",
            conversation=conv,
        )
        conv.delete()
        self.assertEqual(Notification.objects.count(), 0)
