from workspace.users.tests.settings_api import SettingsApiTestCase

# What preferences.js holds as its defaults and sends back whole.
PAGE_PREFERENCES = {
    "showHiddenFiles": False,
    "confirmBeforeDelete": True,
    "defaultSort": "default",
    "defaultSortDir": "asc",
    "breadcrumbCollapse": 4,
    "defaultViewMode": "list",
    "mosaicTileSize": 3,
    "showPinned": True,
    "showGroups": True,
    "showTags": True,
    "compactList": False,
    "nameCollision": "ask",
}


class FilesSettingsTests(SettingsApiTestCase):
    module = "files"

    def test_the_preferences_the_page_sends_are_stored_whole(self):
        self.assertAccepts(
            "preferences",
            [PAGE_PREFERENCES, {**PAGE_PREFERENCES, "defaultViewMode": "mosaic"}],
        )

    def test_a_preference_takes_the_values_its_control_offers(self):
        self.assertRefuses(
            "preferences",
            [
                {"defaultViewMode": "carousel"},
                {"defaultSort": "colour"},
                {"defaultSortDir": "sideways"},
                {"breadcrumbCollapse": 1},
                {"breadcrumbCollapse": 9},
                {"mosaicTileSize": 6},
                {"nameCollision": "panic"},
                {"showHiddenFiles": "sometimes"},
                ["mosaic"],
                "mosaic",
            ],
        )

    def test_an_option_the_panel_no_longer_has_is_left_out(self):
        self.put("preferences", {"compactList": True, "thumbnailCache": "big"})
        self.assertEqual(self.stored()["preferences"], {"compactList": True})

    def test_the_table_controls_are_columns(self):
        self.assertAccepts(
            "table_controls",
            [
                {
                    "columnOrder": ["name", "size", "modified"],
                    "columnVisibility": {"size": True, "owner": False},
                }
            ],
        )
        self.assertRefuses(
            "table_controls",
            [
                {"columnOrder": "name"},
                {"columnOrder": [f"column-{n}" for n in range(65)]},
                {"columnVisibility": {"size": "sometimes"}},
                {"columnVisibility": {f"column-{n}": True for n in range(65)}},
            ],
        )

    def test_the_editor_options_are_bounded(self):
        self.assertAccepts(
            "editor",
            [
                {
                    "wordWrap": True,
                    "minimap": False,
                    "fontSize": 14,
                    "tabSize": 4,
                    "showWhitespace": False,
                    "lineNumbers": True,
                }
            ],
        )
        self.assertRefuses(
            "editor", [{"fontSize": 400}, {"fontSize": 2}, {"tabSize": 0}, [14]]
        )
