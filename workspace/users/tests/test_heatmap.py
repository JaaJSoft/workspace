from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as dj_timezone
from django.utils import translation

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


class HeatmapLanguageTests(TestCase):
    def test_labels_follow_the_active_language(self):
        user = User.objects.create_user(username="heat", password="p")
        fixed_now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        with (
            patch("django.utils.timezone.now", return_value=fixed_now),
            translation.override("fr"),
        ):
            data = _build_heatmap_data(user.id)
            labels = [str(label) for label in data["month_labels"] if label]
            # June and July stay apart, unlike Django's "M" format ("Jui", "Jul").
            self.assertIn("juin", labels)
            self.assertIn("juil.", labels)
            self.assertEqual(
                data["weeks"][-1][2]["label"], "0 contribution le 15 juillet 2026"
            )
