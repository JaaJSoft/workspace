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

    def test_event_reaches_the_owner_once(self):
        provider = FilesSSEProvider(self.owner, None)
        push_file_event(self.file, "file.updated", "owner")

        events = provider.poll("dirty")
        self.assertEqual(len(events), 1)
        name, payload, event_id = events[0]
        self.assertEqual(name, "file.updated")
        self.assertEqual(payload["file_uuid"], str(self.file.uuid))
        self.assertEqual(payload["actor"], "owner")
        self.assertEqual(event_id, "1")
        self.assertEqual(provider.poll("dirty"), [])

    def test_every_open_stream_of_the_owner_receives_the_event(self):
        """Two tabs used to race for it; the first drain emptied the mailbox."""
        first_tab = FilesSSEProvider(self.owner, None)
        second_tab = FilesSSEProvider(self.owner, None)
        push_file_event(self.file, "file.updated", "owner")

        self.assertEqual(len(first_tab.poll("dirty")), 1)
        self.assertEqual(len(second_tab.poll("dirty")), 1)

    def test_a_reconnecting_stream_replays_what_it_missed(self):
        """Nothing else has to be pushed for the backlog to come out."""
        push_file_event(self.file, "file.updated", "owner")
        push_file_event(self.file, "file.locked", "owner")

        resumed = FilesSSEProvider(self.owner, "1")
        events = resumed.get_initial_events()
        self.assertEqual([name for name, _payload, _id in events], ["file.locked"])

    def test_an_idle_poll_still_reads_the_mailbox(self):
        """A push that raced the Pub/Sub subscribe must not wait for the next one."""
        provider = FilesSSEProvider(self.owner, None)
        push_file_event(self.file, "file.updated", "owner")
        self.assertEqual(len(provider.poll(None)), 1)

    def test_excluded_user_and_strangers_get_nothing(self):
        owner_stream = FilesSSEProvider(self.owner, None)
        other_stream = FilesSSEProvider(self.other, None)
        push_file_event(
            self.file, "file.updated", "owner", exclude_user_id=self.owner.id
        )
        self.assertEqual(owner_stream.poll("dirty"), [])
        self.assertEqual(other_stream.poll("dirty"), [])
