from datetime import timedelta

from django.conf import settings
from django.test import SimpleTestCase

from workspace.common.redaction import is_sensitive_name


class MeetingSettingsTests(SimpleTestCase):
    def test_defaults_are_timedeltas(self):
        self.assertEqual(settings.MEETING_LOBBY_LEAD, timedelta(minutes=15))
        self.assertEqual(settings.MEETING_GRACE, timedelta(minutes=30))
        self.assertEqual(settings.MEETING_DEFAULT_DURATION, timedelta(minutes=60))

    def test_waiting_guest_cap_is_an_int(self):
        self.assertEqual(settings.MEETING_MAX_WAITING_GUESTS, 20)


class TokenHashRedactionTests(SimpleTestCase):
    def test_token_hash_is_redacted(self):
        self.assertTrue(is_sensitive_name("token_hash"))
