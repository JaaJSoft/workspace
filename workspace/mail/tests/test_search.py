from datetime import UTC, datetime

from django.test import TestCase
from django.utils import timezone as dj_timezone

from workspace.mail.search import _format_date


class FormatDateTimezoneTests(TestCase):
    def tearDown(self):
        dj_timezone.deactivate()

    def test_old_date_formats_in_active_timezone(self):
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        dt = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        dj_timezone.activate("Europe/Paris")
        self.assertEqual(_format_date(dt), "01 Feb")
