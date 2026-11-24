from workspace.users.tests.settings_api import SettingsApiTestCase

# What calendar.js holds as its defaults and sends back whole.
PAGE_PREFERENCES = {
    "defaultView": "dayGridMonth",
    "firstDay": 1,
    "weekNumbers": False,
    "timeFormat": "24h",
    "defaultAllDay": False,
    "showDeclined": False,
    "notifyPollVotes": True,
    "showTasks": True,
}


class CalendarSettingsTests(SettingsApiTestCase):
    module = "calendar"

    def test_the_preferences_the_page_sends_are_stored_whole(self):
        self.assertAccepts(
            "preferences",
            [
                PAGE_PREFERENCES,
                {**PAGE_PREFERENCES, "defaultView": "agenda", "firstDay": 0},
            ],
        )

    def test_a_preference_takes_the_values_its_control_offers(self):
        self.assertRefuses(
            "preferences",
            [
                {"defaultView": "listWeek"},
                {"firstDay": 7},
                {"timeFormat": "36h"},
                {"notifyPollVotes": "rarely"},
                [1, 2],
                3,
            ],
        )
