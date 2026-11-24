from workspace.users.tests.settings_api import SettingsApiTestCase


class DashboardSettingsTests(SettingsApiTestCase):
    module = "dashboard"

    def test_the_temperature_unit_is_one_the_forecast_takes(self):
        self.assertAccepts("temperature_unit", ["celsius", "fahrenheit"])
        self.assertRefuses("temperature_unit", ["kelvin", 1])

    def test_the_hidden_lists_are_lists_of_slugs(self):
        for key in ("hidden_today_sources", "hidden_modules"):
            self.assertAccepts(key, [["mail", "tasks"], []])
            self.assertRefuses(
                key,
                [1, "mail", {"mail": True}, ["mail", "not a slug"], ["mail", ["x"]]],
            )

    def test_a_hidden_list_is_bounded(self):
        self.assertRefuses("hidden_modules", [[f"module-{n}" for n in range(65)]])
