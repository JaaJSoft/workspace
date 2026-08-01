from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as dj_timezone

from workspace.users.ui.views import _build_heatmap_data

User = get_user_model()


class HeatmapTimezoneTests(TestCase):
    def tearDown(self):
        dj_timezone.deactivate()

    def test_today_bucket_uses_active_timezone(self):
        user = User.objects.create_user(username="heat", password="p")
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        fixed_now = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        dj_timezone.activate("Europe/Paris")
        with patch("django.utils.timezone.now", return_value=fixed_now):
            data = _build_heatmap_data(user.id)
        last_day = data["weeks"][-1][-1]["date"]
        self.assertEqual(last_day, "2026-02-01")
