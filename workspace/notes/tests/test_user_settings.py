import uuid

from workspace.users.tests.settings_api import SettingsApiTestCase

# What notes.js holds as its defaults and sends back whole, the two folders
# aside: they are null until the page has created them.
PAGE_PREFERENCES = {
    "showTags": True,
    "compactList": False,
    "showFolders": True,
    "showJournal": True,
    "showGroupFolders": True,
    "defaultView": "all",
    "sortBy": "modified",
    "confirmBeforeDelete": True,
    "hiddenItems": [],
    "showHidden": False,
}


class NotesSettingsTests(SettingsApiTestCase):
    module = "notes"

    def test_the_preferences_the_page_sends_are_stored_whole(self):
        folders = {
            "defaultFolderUuid": str(uuid.uuid4()),
            "journalFolderUuid": str(uuid.uuid4()),
        }
        self.assertAccepts(
            "preferences",
            [
                PAGE_PREFERENCES,
                {**PAGE_PREFERENCES, **folders, "hiddenItems": [str(uuid.uuid4())]},
            ],
        )

    def test_a_folder_not_chosen_yet_is_left_out(self):
        sent = {
            **PAGE_PREFERENCES,
            "defaultFolderUuid": None,
            "journalFolderUuid": None,
        }
        self.assertEqual(self.put("preferences", sent).status_code, 200)
        self.assertEqual(self.stored()["preferences"], PAGE_PREFERENCES)

    def test_a_preference_takes_the_values_its_control_offers(self):
        self.assertRefuses(
            "preferences",
            [
                {"defaultView": "kanban"},
                {"sortBy": "mood"},
                {"journalFolderUuid": "the usual one"},
                {"hiddenItems": ["not-a-uuid"]},
                {"hiddenItems": "all"},
                ["journal"],
            ],
        )
