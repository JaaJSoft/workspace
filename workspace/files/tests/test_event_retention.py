from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import File, FileEvent
from workspace.files.services import events
from workspace.files.tasks import purge_events

User = get_user_model()

SHOWN = 3


@override_settings(FILES_EVENT_RETENTION_DAYS=365)
@patch.object(events, "TIMELINE_EVENTS_LIMIT", SHOWN)
class PurgeExpiredEventsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="keeper", password="pw")
        self.file = self._file("notes.md")

    def _file(self, name):
        return File.objects.create(
            owner=self.user, name=name, node_type=File.NodeType.FILE
        )

    def _event(self, file_obj, days_ago, action=FileEvent.Action.RENAMED):
        event = FileEvent.objects.create(file=file_obj, actor=self.user, action=action)
        FileEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        return event.pk

    def _left(self, file_obj):
        return set(FileEvent.objects.filter(file=file_obj).values_list("pk", flat=True))

    def test_old_events_past_the_timeline_are_purged(self):
        old = [self._event(self.file, 400 + n) for n in range(3)]
        shown = [self._event(self.file, n) for n in range(SHOWN)]

        self.assertEqual(purge_events(), {"deleted": len(old)})
        self.assertEqual(self._left(self.file), set(shown))

    def test_old_events_the_timeline_still_lists_are_kept(self):
        kept = [self._event(self.file, 400 + n) for n in range(SHOWN)]

        self.assertEqual(events.purge_expired_events(), 0)
        self.assertEqual(self._left(self.file), set(kept))

    def test_recent_events_past_the_timeline_are_kept(self):
        recent = [self._event(self.file, n) for n in range(SHOWN + 4)]

        self.assertEqual(events.purge_expired_events(), 0)
        self.assertEqual(self._left(self.file), set(recent))

    def test_the_newest_old_events_fill_a_short_timeline(self):
        recent = self._event(self.file, 1)
        old = [self._event(self.file, 400 + n) for n in range(SHOWN + 2)]

        events.purge_expired_events()

        self.assertEqual(self._left(self.file), {recent, *old[: SHOWN - 1]})

    def test_each_file_keeps_its_own_timeline(self):
        other = self._file("other.md")
        for n in range(SHOWN + 1):
            self._event(self.file, 400 + n)
        quiet = [self._event(other, 400 + n) for n in range(SHOWN)]

        self.assertEqual(events.purge_expired_events(), 1)
        self.assertEqual(self._left(other), set(quiet))

    @override_settings(FILES_EVENT_RETENTION_DAYS=30)
    def test_the_retention_is_a_setting(self):
        for n in range(SHOWN):
            self._event(self.file, n)
        stale = self._event(self.file, 40)

        events.purge_expired_events()

        self.assertNotIn(stale, self._left(self.file))
