from workspace.users.tests.settings_api import SettingsApiTestCase


class VaultSettingsTests(SettingsApiTestCase):
    module = "vault"

    def test_the_lock_delay_is_a_delay(self):
        # A vault that never locks is the failure a bad value here causes.
        self.assertAccepts("lock_after_minutes", [1, 5, 60, 1440])
        self.assertRefuses("lock_after_minutes", [0, -1, 7.5, True, 100000, "never"])

    def test_the_default_sort_is_one_the_panel_offers(self):
        # Any other reaches the listing as a sort nothing implements.
        self.assertAccepts("default_sort", ["default", "name", "favorite", "created"])
        self.assertRefuses("default_sort", ["size", "", 3, "NAME"])
