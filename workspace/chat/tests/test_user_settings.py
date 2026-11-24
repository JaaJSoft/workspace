from workspace.users.tests.settings_api import SettingsApiTestCase

# What chat_preferences.js holds as its defaults and sends back whole.
PAGE_PREFERENCES = {
    "compactConversationList": False,
    "compactMessageView": False,
    "showThreadRepliesInline": False,
    "messageAnimation": "slide",
    "composerMode": "rendered",
}


class ChatSettingsTests(SettingsApiTestCase):
    module = "chat"

    def test_the_preferences_the_page_sends_are_stored_whole(self):
        self.assertAccepts(
            "preferences",
            [PAGE_PREFERENCES, {**PAGE_PREFERENCES, "composerMode": "markdown"}],
        )

    def test_a_preference_takes_the_values_its_control_offers(self):
        self.assertRefuses(
            "preferences",
            [
                {"messageAnimation": "explode"},
                {"composerMode": "wysiwyg"},
                {"compactMessageView": "a bit"},
                "compact",
            ],
        )

    def test_call_sounds_are_on_or_off(self):
        self.assertAccepts("call_sounds", [False, True])
        self.assertRefuses("call_sounds", ["loud", 11])
