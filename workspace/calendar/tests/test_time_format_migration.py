import importlib
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from workspace.users.models import UserSetting

# The module name starts with a digit: not reachable with a plain import.
migration = importlib.import_module(
    "workspace.calendar.migrations.0023_time_format_to_core"
)

User = get_user_model()


class TimeFormatToCoreTests(TestCase):
    def _migrate(self):
        migration.forwards(apps, SimpleNamespace(connection=connection))

    def _clock(self, user):
        row = UserSetting.objects.filter(
            user=user, module="core", key="time_format"
        ).first()
        return row and row.value

    def _calendar_prefs(self, user, value):
        UserSetting.objects.create(
            user=user, module="calendar", key="preferences", value=value
        )

    def test_a_12h_calendar_becomes_a_12h_app(self):
        twelve = User.objects.create_user(username="twelve")
        twenty_four = User.objects.create_user(username="twentyfour")
        untouched = User.objects.create_user(username="untouched")
        self._calendar_prefs(twelve, {"timeFormat": "12h", "firstDay": 0})
        self._calendar_prefs(twenty_four, {"timeFormat": "24h"})
        self._calendar_prefs(untouched, {"firstDay": 1})

        self._migrate()

        self.assertEqual(self._clock(twelve), "12h")
        self.assertIsNone(self._clock(twenty_four))
        self.assertIsNone(self._clock(untouched))

    def test_an_app_wide_choice_already_made_is_kept(self):
        user = User.objects.create_user(username="chosen")
        self._calendar_prefs(user, {"timeFormat": "12h"})
        UserSetting.objects.create(
            user=user, module="core", key="time_format", value="24h"
        )

        self._migrate()

        self.assertEqual(self._clock(user), "24h")
