from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import FileService
from workspace.files.sse_provider import FilesSSEProvider, push_file_event

User = get_user_model()


class FilesSseMailboxTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        self.file = FileService.create_file(
            self.owner, "a.txt", None, content=ContentFile(b"a")
        )

    def tearDown(self):
        cache.clear()

    def test_event_reaches_the_owner_once_and_is_drained(self):
        push_file_event(self.file, "file.updated", "owner")
        provider = FilesSSEProvider(self.owner, None)
        events = provider.poll("dirty")
        self.assertEqual(len(events), 1)
        name, payload, _ = events[0]
        self.assertEqual(name, "file.updated")
        self.assertEqual(payload["file_uuid"], str(self.file.uuid))
        self.assertEqual(payload["actor"], "owner")
        self.assertEqual(provider.poll("dirty"), [])

    def test_poll_without_dirty_flag_reads_nothing(self):
        push_file_event(self.file, "file.updated", "owner")
        self.assertEqual(FilesSSEProvider(self.owner, None).poll(None), [])

    def test_excluded_user_and_strangers_get_nothing(self):
        push_file_event(
            self.file, "file.updated", "owner", exclude_user_id=self.owner.id
        )
        self.assertEqual(FilesSSEProvider(self.owner, None).poll("dirty"), [])
        self.assertEqual(FilesSSEProvider(self.other, None).poll("dirty"), [])
