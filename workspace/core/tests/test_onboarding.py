from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from workspace.core import changelog as changelog_module
from workspace.core.context_processors import workspace_modules
from workspace.core.setting_keys import (
    CHANGELOG_LAST_SEEN_VERSION,
    MODULE,
    ONBOARDING_COMPLETED,
)
from workspace.users.services.settings import set_setting

User = get_user_model()


def _stub_changelog_with_latest(test, latest="0.20.0"):
    """Patch get_changelog_entries with a single fake entry so CHANGELOG_UNREAD can be exercised."""
    entries = [{"version": latest, "title": "Stub", "html": "<p>x</p>"}]
    patcher = patch.object(
        changelog_module,
        "get_changelog_entries",
        return_value=entries,
    )
    patcher.start()
    test.addCleanup(patcher.stop)


class OnboardingContextProcessorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="alice", password="pass")
        _stub_changelog_with_latest(self)

    def tearDown(self):
        cache.clear()

    def _request(self, user):
        req = self.factory.get("/")
        req.user = user
        return req

    def test_anonymous_user_has_no_onboarding_flag(self):
        ctx = workspace_modules(self._request(AnonymousUser()))
        self.assertFalse(ctx["ONBOARDING_PENDING"])
        self.assertFalse(ctx["CHANGELOG_UNREAD"])

    def test_user_without_setting_is_pending(self):
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx["ONBOARDING_PENDING"])

    def test_user_with_setting_false_is_pending(self):
        set_setting(
            self.user,
            MODULE,
            ONBOARDING_COMPLETED,
            False,
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx["ONBOARDING_PENDING"])

    def test_user_with_setting_true_is_not_pending(self):
        set_setting(
            self.user,
            MODULE,
            ONBOARDING_COMPLETED,
            True,
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx["ONBOARDING_PENDING"])

    def test_pending_onboarding_suppresses_changelog_unread(self):
        # Brand-new user has unread changelog AND pending onboarding; the
        # onboarding modal owns the first-load slot, so the changelog
        # should NOT auto-open over it.
        ctx = workspace_modules(self._request(self.user))
        self.assertTrue(ctx["ONBOARDING_PENDING"])
        self.assertFalse(ctx["CHANGELOG_UNREAD"])

    def test_completed_onboarding_lets_changelog_unread_through(self):
        set_setting(
            self.user,
            MODULE,
            ONBOARDING_COMPLETED,
            True,
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx["ONBOARDING_PENDING"])
        self.assertTrue(ctx["CHANGELOG_UNREAD"])

    def test_completed_onboarding_and_seen_changelog_is_quiet(self):
        set_setting(
            self.user,
            MODULE,
            ONBOARDING_COMPLETED,
            True,
        )
        set_setting(
            self.user,
            MODULE,
            CHANGELOG_LAST_SEEN_VERSION,
            "0.20.0",
        )
        ctx = workspace_modules(self._request(self.user))
        self.assertFalse(ctx["ONBOARDING_PENDING"])
        self.assertFalse(ctx["CHANGELOG_UNREAD"])

    def test_module_card_links_to_its_url(self):
        html = self._render_onboarding(
            [
                {
                    "name": "Files",
                    "description": "Your files",
                    "icon": "folder",
                    "color": "primary",
                    "url": "/files/",
                }
            ]
        )
        self.assertIn('href="/files/"', html)
        self.assertIn("goTo('/files/')", html)

    def test_module_without_url_is_not_rendered_as_link(self):
        html = self._render_onboarding(
            [
                {
                    "name": "Coming soon",
                    "description": "Not ready yet",
                    "icon": "box",
                    "color": "primary",
                    "url": None,
                }
            ]
        )
        self.assertIn("Coming soon", html)
        # The guard must not emit a broken href or a goTo() to a null url.
        self.assertNotIn('href="None"', html)
        self.assertNotIn("goTo('None')", html)

    def test_welcome_step_greets_user_by_first_name(self):
        self.user.first_name = "Alice"
        self.user.save(update_fields=["first_name"])
        html = self._render_onboarding([])
        self.assertIn("Welcome, Alice", html)

    def test_welcome_step_without_first_name_has_no_dangling_comma(self):
        # user "alice" is created without a first_name in setUp.
        html = self._render_onboarding([])
        self.assertNotIn("Welcome,", html)

    def _render_onboarding(self, modules, pending=True):
        req = self.factory.get("/")
        req.user = self.user
        return render_to_string(
            "core/partials/onboarding.html",
            {
                "ONBOARDING_PENDING": pending,
                "workspace_active_modules": modules,
            },
            request=req,
        )

    def test_reads_core_settings_in_a_single_query(self):
        # Onboarding completed so the changelog branch also reads a core
        # setting: both keys must come from one query, not two.
        set_setting(self.user, MODULE, ONBOARDING_COMPLETED, True)
        set_setting(self.user, MODULE, CHANGELOG_LAST_SEEN_VERSION, "0.19.0")
        # Cold the cache so reads hit the database.
        cache.clear()

        with CaptureQueriesContext(connection) as ctx:
            workspace_modules(self._request(self.user))

        setting_queries = [
            q["sql"] for q in ctx.captured_queries if "users_usersetting" in q["sql"]
        ]
        self.assertEqual(
            len(setting_queries),
            1,
            f"expected a single users_usersetting query, got "
            f"{len(setting_queries)}:\n" + "\n".join(setting_queries),
        )
