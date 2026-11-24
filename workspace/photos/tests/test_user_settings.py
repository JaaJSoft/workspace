import uuid

from workspace.photos.services.import_folder import IMPORT_FOLDER
from workspace.users.tests.settings_api import SettingsApiTestCase


class PhotosSettingsTests(SettingsApiTestCase):
    module = "photos"

    def test_the_tile_size_is_a_step_of_the_slider(self):
        self.assertAccepts("tile_size", [1, 2, 3, 4, 5])
        self.assertRefuses("tile_size", [0, 6, -1, 2.5, True, "large"])

    def test_the_choices_of_the_preferences_panel(self):
        self.assertAccepts("tile_shape", ["square", "original"])
        self.assertRefuses("tile_shape", ["round", 1])
        self.assertAccepts("default_media_type", ["all", "photo", "video"])
        self.assertRefuses("default_media_type", ["audio", 1])

    def test_the_default_library_is_a_scope_or_a_group(self):
        self.assertAccepts("default_scope", ["mine", "all", "shared", "group:12"])
        self.assertRefuses("default_scope", ["group:", "group:x", "family", 3])

    def test_the_switches_are_on_or_off(self):
        for key in (
            "tile_badges",
            "video_hover_preview",
            "import_by_date",
            "show_hidden",
            "auto_tags",
            "show_map",
            "faces_enabled",
        ):
            self.assertAccepts(key, [True, False])
            self.assertRefuses(key, ["sometimes", 7, []])

    def test_the_import_folder_goes_through_its_own_endpoint(self):
        # That endpoint checks the folder is one the user may write into.
        response = self.put(IMPORT_FOLDER, str(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.stored(), {})
