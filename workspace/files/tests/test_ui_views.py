from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.users.services.settings import set_setting

User = get_user_model()


class FilesIndexSettingsTests(TestCase):
    """The file browser view reads per-user 'preferences' and 'viewer'
    settings to populate its context. Both live in the ``files`` module, so
    they should be fetched in a single query, not one per key."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="prefs_user",
            email="prefs@test.com",
            password="x",
        )
        self.client.force_login(self.user)

    def tearDown(self):
        # set_setting populates the process-global LocMemCache, which is not
        # reset between TestCase runs. Clear it to keep tests order-independent.
        cache.clear()

    def test_index_exposes_file_and_viewer_preferences(self):
        """Both settings flow into the template context with the right values."""
        set_setting(self.user, "files", "preferences", {"breadcrumbCollapse": 2})
        set_setting(self.user, "files", "viewer", {"theme": "dark"})

        response = self.client.get(reverse("files_ui:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["breadcrumb_collapse"], 2)
        self.assertEqual(response.context["file_prefs"], {"breadcrumbCollapse": 2})
        self.assertEqual(response.context["viewer_prefs"], {"theme": "dark"})

    def test_index_defaults_when_settings_absent(self):
        """Missing settings fall back to the documented defaults."""
        response = self.client.get(reverse("files_ui:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["breadcrumb_collapse"], 4)
        self.assertEqual(response.context["file_prefs"], {})
        self.assertEqual(response.context["viewer_prefs"], {})

    def test_index_loads_files_settings_in_a_single_query(self):
        """Cold cache: the two files settings are read in one DB round-trip."""
        set_setting(self.user, "files", "preferences", {"breadcrumbCollapse": 3})
        set_setting(self.user, "files", "viewer", {"theme": "light"})
        # Cold the cache so reads hit the database.
        cache.clear()

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse("files_ui:index"))

        self.assertEqual(response.status_code, 200)
        # Only count reads scoped to the files module; the core context
        # processors issue their own usersetting reads (theme, timezone, ...)
        # which are out of scope here.
        setting_queries = [
            q["sql"]
            for q in ctx.captured_queries
            if "users_usersetting" in q["sql"] and "\"module\" = 'files'" in q["sql"]
        ]
        self.assertEqual(
            len(setting_queries),
            1,
            f"expected a single files users_usersetting query, got "
            f"{len(setting_queries)}:\n" + "\n".join(setting_queries),
        )
