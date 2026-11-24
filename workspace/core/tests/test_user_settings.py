import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from workspace.core import setting_keys
from workspace.users.services.settings import set_setting
from workspace.users.settings_registry import user_settings_registry
from workspace.users.tests.settings_api import SettingsApiTestCase

WORKSPACE = Path(settings.BASE_DIR) / "workspace"

# A settings URL whose module and key are both spelled out.
_API_URL = re.compile(r"/api/v1/settings/([a-z_]+)/([a-z_]+)(?![a-z_]*['\"`]?\s*\+)")
# get_setting(user, "module", "key"), and its set and delete counterparts.
_SERVICE_CALL = re.compile(
    r"\b(?:get|set|delete)_setting\(\s*[\w.]+,\s*[\"'](\w+)[\"'],\s*[\"'](\w+)[\"']"
)
_SIDEBAR_SAVE = re.compile(r"sidebarPreference\.save\('(\w+)'")


def _sources(*suffixes):
    for path in WORKSPACE.rglob("*"):
        if path.suffix not in suffixes or not path.is_file():
            continue
        parts = path.relative_to(WORKSPACE).parts
        if "tests" in parts or "vendor" in parts or "migrations" in parts:
            continue
        yield path, path.read_text(encoding="utf-8", errors="ignore")


class EverySettingIsDeclaredTests(SimpleTestCase):
    """A setting the code reads or writes is one a module declared.

    An undeclared key is a 404 on the API, which a page writing it with a
    fire-and-forget fetch never notices: the preference just stops sticking.
    """

    def setUp(self):
        self.declared = user_settings_registry.declared()

    def assertDeclared(self, module, key, path):
        self.assertIn(
            key,
            self.declared.get(module, ()),
            f"{path.relative_to(WORKSPACE)} uses the setting {module}/{key}, which "
            f"workspace/{module}/user_settings.py does not declare",
        )

    def test_the_settings_a_page_writes(self):
        found = 0
        for path, text in _sources(".js", ".html"):
            for module, key in _API_URL.findall(text):
                found += 1
                self.assertDeclared(module, key, path)
        self.assertGreater(found, 15)

    def test_the_settings_the_server_reads_and_writes(self):
        found = 0
        for path, text in _sources(".py"):
            for module, key in _SERVICE_CALL.findall(text):
                found += 1
                self.assertDeclared(module, key, path)
        self.assertGreater(found, 10)

    def test_the_modules_whose_sidebar_folds(self):
        modules = set()
        for path, text in _sources(".js", ".html"):
            for module in _SIDEBAR_SAVE.findall(text):
                modules.add(module)
                self.assertDeclared(module, setting_keys.SIDEBAR_COLLAPSED, path)
        self.assertGreater(len(modules), 5)

    def test_the_keys_core_names(self):
        for key in (
            setting_keys.CHANGELOG_LAST_SEEN_VERSION,
            setting_keys.ONBOARDING_COMPLETED,
            setting_keys.SEARCH_SCOPE,
            setting_keys.SENSITIVE_BLUR,
            setting_keys.REVEAL_SENSITIVE_ON_HOVER,
            setting_keys.REVEAL_SENSITIVE_IN_VIEWER,
        ):
            self.assertIn(key, self.declared[setting_keys.MODULE])


class CoreSettingsTests(SettingsApiTestCase):
    module = "core"

    def test_a_theme_is_a_theme_name(self):
        for key in ("theme", "light_theme", "dark_theme"):
            self.assertAccepts(key, ["dark", "dracula", "nord"])
            self.assertRefuses(key, ["Dark Mode", "x" * 41, '"><script>', ["dark"]])

    def test_a_timezone_is_one_the_database_knows(self):
        self.assertAccepts("timezone", ["Europe/Paris", "UTC", "America/New_York"])
        self.assertRefuses("timezone", ["Mars/Olympus", "../etc", "Europe", 7])

    def test_a_time_format_is_a_24h_or_12h_clock(self):
        self.assertAccepts("time_format", ["24h", "12h"])
        self.assertRefuses("time_format", ["36h", "auto", "", 12])

    def test_a_language_is_one_the_deployment_offers(self):
        self.assertAccepts("language", ["fr", "en"])
        # A regional variant would be stored, then read as English.
        self.assertRefuses("language", ["de", "fr-CA", ""])

    def test_a_search_scope_is_one_the_search_knows(self):
        self.assertAccepts("search_scope", ["mine", "all"])
        self.assertRefuses("search_scope", ["shared", 1])

    def test_the_sensitive_blur_and_its_softenings(self):
        self.assertAccepts("sensitive_blur", ["strict", "standard", "off"])
        self.assertRefuses("sensitive_blur", ["blurry", 3])
        for key in ("reveal_sensitive_on_hover", "reveal_sensitive_in_viewer"):
            self.assertAccepts(key, [True, False])
            self.assertRefuses(key, ["maybe", 7, []])

    def test_the_onboarding_can_be_marked_done(self):
        self.assertAccepts("onboarding_completed", [True])

    def test_the_changelog_marker_is_the_server_s_to_write(self):
        set_setting(self.user, "core", "changelog_last_seen_version", "1.2.0")
        response = self.put("changelog_last_seen_version", "99.0.0")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.stored()["changelog_last_seen_version"], "1.2.0")

    def test_a_bulk_write_with_one_bad_value_writes_nothing(self):
        response = self.client.patch(
            "/api/v1/settings/core",
            data={"theme": "dark", "timezone": "Not/AZone"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored(), {})


class SidebarSettingTests(SettingsApiTestCase):
    def test_every_module_with_a_sidebar_takes_the_folded_flag(self):
        for module in (
            "calendar",
            "chat",
            "files",
            "mail",
            "notes",
            "people",
            "photos",
            "projects",
            "vault",
        ):
            self.assertAccepts("sidebar_collapsed", [True, False], module)
            self.assertRefuses("sidebar_collapsed", ["folded", 7], module)

    def test_a_module_without_a_sidebar_has_no_such_setting(self):
        response = self.put("sidebar_collapsed", True, "dashboard")
        self.assertEqual(response.status_code, 404)
