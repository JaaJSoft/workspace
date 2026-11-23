import uuid

from workspace.users.tests.settings_api import SettingsApiTestCase


class ProjectsSettingsTests(SettingsApiTestCase):
    module = "projects"

    def test_the_reminder_hour_is_an_hour_of_the_day(self):
        self.assertAccepts("reminder_hour", [0, 8, 23])
        self.assertRefuses("reminder_hour", [-1, 24, 7.5, True, "noon"])

    def test_the_notification_level_is_one_of_the_three(self):
        self.assertAccepts("notify_level", ["all", "in_app", "none"])
        self.assertRefuses("notify_level", ["loud", 1])

    def test_auto_watch_is_on_or_off(self):
        self.assertAccepts("auto_watch", [False, True])
        self.assertRefuses("auto_watch", ["sometimes", 7])

    def test_the_last_project_is_the_server_s_to_write(self):
        response = self.put("last_project", str(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.stored(), {})
