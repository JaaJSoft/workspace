from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import File, FileEvent
from workspace.files.services.scanning.scan_events import scan_file_for_event

User = get_user_model()
ENABLED = {"FILES_MALWARE_SCAN_ENABLED": True}


class ScanEventHandlerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="evt", password="p")

    def _event(self, action, node_type=File.NodeType.FILE, deleted=False):
        f = File(owner=self.user, name="a.txt", node_type=node_type)
        if node_type == File.NodeType.FILE:
            f.content = ContentFile(b"x", name="a.txt")
            f.size = 1
        f.save()
        if deleted:
            from django.utils import timezone

            f.deleted_at = timezone.now()
            f.save(update_fields=["deleted_at"])
        return FileEvent.objects.create(file=f, actor=self.user, action=action)

    def test_created_enqueues_a_scan(self):
        event = self._event(FileEvent.Action.CREATED)
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            scan_file_for_event(event)
        delay.assert_called_once_with(str(event.file_id))

    def test_content_replaced_enqueues_a_scan(self):
        event = self._event(FileEvent.Action.CONTENT_REPLACED)
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            scan_file_for_event(event)
        delay.assert_called_once()

    def test_disabled_enqueues_nothing(self):
        event = self._event(FileEvent.Action.CREATED)
        with (
            override_settings(FILES_MALWARE_SCAN_ENABLED=False),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            scan_file_for_event(event)
        delay.assert_not_called()

    def test_folder_enqueues_nothing(self):
        event = self._event(FileEvent.Action.CREATED, node_type=File.NodeType.FOLDER)
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            scan_file_for_event(event)
        delay.assert_not_called()

    def test_trashed_file_enqueues_nothing(self):
        event = self._event(FileEvent.Action.CREATED, deleted=True)
        with (
            override_settings(**ENABLED),
            patch("workspace.files.tasks.scan_file.delay") as delay,
        ):
            scan_file_for_event(event)
        delay.assert_not_called()

    def test_handler_is_registered_for_both_write_actions(self):
        from workspace.files.services import event_dispatch

        for action in (FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED):
            names = [
                getattr(h, "__name__", "")
                for h in event_dispatch._HANDLERS.get(str(action), [])
            ]
            self.assertIn("scan_file_for_event", names)
