from workspace.mail.services.ai_settings import MAIL_AI_FEATURES
from workspace.mail.services.notifications import HARD_MAX_NOTIFY_BURST
from workspace.users.tests.settings_api import SettingsApiTestCase

# What mail_preferences.js holds as its defaults and sends back whole.
PAGE_PREFERENCES = {
    "density": "normal",
    "previewLines": 1,
    "confirmBeforeDelete": True,
    "showLabels": True,
    "rulesCompact": False,
}


class MailSettingsTests(SettingsApiTestCase):
    module = "mail"

    def test_the_preferences_the_page_sends_are_stored_whole(self):
        self.assertAccepts(
            "preferences",
            [PAGE_PREFERENCES, {**PAGE_PREFERENCES, "density": "compact"}],
        )

    def test_a_preference_takes_the_values_its_control_offers(self):
        self.assertRefuses(
            "preferences",
            [{"density": "airy"}, {"previewLines": 3}, {"showLabels": "some"}, [1]],
        )

    def test_every_ai_feature_has_its_switch(self):
        for feature in MAIL_AI_FEATURES:
            self.assertAccepts(f"ai_{feature}", [False, True])
            self.assertRefuses(f"ai_{feature}", ["maybe", 7])
        self.assertEqual(self.put("ai_telepathy", True).status_code, 404)

    def test_the_notification_mode_is_one_the_sync_knows(self):
        self.assertAccepts("notify_mode", ["all", "labels", "never"])
        self.assertRefuses("notify_mode", ["sometimes", 1])

    def test_the_notification_burst_is_bounded(self):
        self.assertAccepts("notify_max_burst", [1, 10, HARD_MAX_NOTIFY_BURST])
        self.assertRefuses(
            "notify_max_burst", [0, -3, HARD_MAX_NOTIFY_BURST + 1, 2.5, True, "many"]
        )
