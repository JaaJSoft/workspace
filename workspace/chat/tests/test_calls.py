from uuid import uuid4

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from workspace.chat.services import calls


class DurationFormatTests(SimpleTestCase):
    def test_seconds(self):
        self.assertEqual(calls.format_duration(0), "0s")
        self.assertEqual(calls.format_duration(45), "45s")

    def test_minutes(self):
        self.assertEqual(calls.format_duration(60), "1 min")
        self.assertEqual(calls.format_duration(12 * 60 + 5), "12 min")

    def test_hours(self):
        self.assertEqual(calls.format_duration(3600), "1 h 00")
        self.assertEqual(calls.format_duration(3665), "1 h 01")


@override_settings(CHAT_CALL_PRESENCE_TTL=12)
class PresenceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.session_id = uuid4()

    def tearDown(self):
        cache.clear()

    def test_touch_then_get(self):
        changed = calls.touch_presence(self.session_id, 1, {"audio": True})
        self.assertTrue(changed)
        self.assertEqual(calls.get_presence(self.session_id), {"1": {"audio": True}})

    def test_touch_same_state_reports_unchanged(self):
        calls.touch_presence(self.session_id, 1, {"audio": True})
        self.assertFalse(calls.touch_presence(self.session_id, 1, {"audio": True}))

    def test_touch_changed_state_reports_changed(self):
        calls.touch_presence(self.session_id, 1, {"audio": True})
        self.assertTrue(calls.touch_presence(self.session_id, 1, {"audio": False}))
