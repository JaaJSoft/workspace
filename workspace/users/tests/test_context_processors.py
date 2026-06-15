from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from workspace.users.context_processors import user_preferences
from workspace.users.services.settings import set_setting

User = get_user_model()


class UserPreferencesContextProcessorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="alice", password="pass")

    def tearDown(self):
        cache.clear()

    def _request(self, user):
        req = self.factory.get("/")
        req.user = user
        return req

    def test_anonymous_returns_empty_dict(self):
        self.assertEqual(user_preferences(self._request(AnonymousUser())), {})

    def test_missing_user_attr_returns_empty_dict(self):
        req = self.factory.get("/")
        self.assertEqual(user_preferences(req), {})

    def test_authenticated_returns_stored_preferences(self):
        set_setting(self.user, "core", "theme", "dracula")
        set_setting(self.user, "core", "light_theme", "nord")
        set_setting(self.user, "core", "dark_theme", "dracula")
        set_setting(self.user, "core", "timezone", "Europe/Paris")

        ctx = user_preferences(self._request(self.user))
        self.assertEqual(
            ctx,
            {
                "user_theme": "dracula",
                "user_light_theme": "nord",
                "user_dark_theme": "dracula",
                "user_timezone": "Europe/Paris",
            },
        )

    def test_authenticated_defaults_when_nothing_stored(self):
        ctx = user_preferences(self._request(self.user))
        self.assertEqual(
            ctx,
            {
                "user_theme": "light",
                "user_light_theme": "light",
                "user_dark_theme": "dark",
                "user_timezone": "",
            },
        )

    def test_light_and_dark_theme_independent_of_active_theme(self):
        # Active theme can be the user's chosen dark theme while the
        # ``light_theme`` slot still points at a different light option.
        set_setting(self.user, "core", "theme", "dracula")
        set_setting(self.user, "core", "light_theme", "cupcake")
        set_setting(self.user, "core", "dark_theme", "dracula")

        ctx = user_preferences(self._request(self.user))
        self.assertEqual(ctx["user_theme"], "dracula")
        self.assertEqual(ctx["user_light_theme"], "cupcake")
        self.assertEqual(ctx["user_dark_theme"], "dracula")

    def test_reads_all_core_settings_in_a_single_query(self):
        set_setting(self.user, "core", "theme", "dracula")
        set_setting(self.user, "core", "light_theme", "nord")
        set_setting(self.user, "core", "dark_theme", "dracula")
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        # Cold the cache so reads hit the database.
        cache.clear()

        with CaptureQueriesContext(connection) as ctx:
            user_preferences(self._request(self.user))

        setting_queries = [
            q["sql"] for q in ctx.captured_queries if "users_usersetting" in q["sql"]
        ]
        self.assertEqual(
            len(setting_queries),
            1,
            f"expected a single users_usersetting query, got "
            f"{len(setting_queries)}:\n" + "\n".join(setting_queries),
        )
