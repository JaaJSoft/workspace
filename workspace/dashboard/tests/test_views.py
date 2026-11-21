import html
import re
from dataclasses import replace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.template import Template
from django.template.defaultfilters import filesizeformat
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from workspace.core.module_registry import ModuleInfo, registry
from workspace.core.today_registry import (
    TodayRows,
    TodaySource,
    today_registry,
)
from workspace.dashboard.services.modules import dashboard_modules, switcher_modules_for
from workspace.dashboard.services.preferences import HIDDEN_TODAY_SOURCES
from workspace.dashboard.views import (
    ACTIVITY_LIMIT,
    _build_dashboard_context,
    _get_activity_context,
)
from workspace.users.services.settings import set_setting

User = get_user_model()


def _mod(slug, active=True, preview=False, show_on_dashboard=True):
    return ModuleInfo(
        name=slug.title(),
        slug=slug,
        description="",
        icon="i",
        color="indigo",
        url=f"/{slug}",
        active=active,
        preview=preview,
        show_on_dashboard=show_on_dashboard,
    )


class UsagePopoverTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="usageuser", password="pw")

    def tearDown(self):
        cache.clear()

    def _render(self):
        from django.template.loader import render_to_string

        context = _build_dashboard_context(self.user, include_activity=False)
        return context["usage_rows"], render_to_string(
            "dashboard/partials/usage_widget.html", context
        )

    def test_every_visible_headline_is_rendered(self):
        rows, page = self._render()

        self.assertTrue(rows)
        self.assertEqual(page.count("data-usage-count="), len(rows))
        for row in rows:
            self.assertIn(f'href="{row["url"]}"', page)
            self.assertIn(f'data-lucide="{row["icon"]}"', page)
            self.assertIn(f"module-{row['color']}", page)
            self.assertIn(row["label"], page)
            self.assertIn(f'data-usage-count="{row["count"]}"', page)

    def test_notes_and_projects_publish_a_row(self):
        rows, _ = self._render()
        self.assertLessEqual({"notes", "projects"}, {row["slug"] for row in rows})

    def test_rows_follow_module_order(self):
        rows, _ = self._render()
        order = {m.slug: m.order for m in registry.get_all()}
        self.assertEqual(
            [row["slug"] for row in rows],
            sorted((row["slug"] for row in rows), key=order.__getitem__),
        )

    def test_provider_without_headline_is_not_rendered(self):
        with patch(
            "workspace.notes.activity.NotesActivityProvider.get_headline",
            return_value=None,
        ):
            rows, page = self._render()

        self.assertNotIn("notes", [row["slug"] for row in rows])
        self.assertNotIn('href="/notes"', page)
        self.assertEqual(page.count("data-usage-count="), len(rows))

    def test_hidden_preview_module_is_left_out(self):
        hidden = replace(registry.get("notes"), preview=True)
        with (
            override_settings(PREVIEW_VISIBILITY="none"),
            patch.dict(registry._modules, {"notes": hidden}),
        ):
            rows, page = self._render()

        self.assertNotIn("notes", [row["slug"] for row in rows])
        self.assertNotIn('href="/notes"', page)

    @patch("workspace.dashboard.views.effective_quota", return_value=2048)
    def test_storage_bar_shows_against_a_quota(self, _quota):
        _, page = self._render()
        self.assertEqual(page.count('x-ref="storageBar"'), 1)
        self.assertIn(f"/ {filesizeformat(2048)}", page)

    @patch("workspace.dashboard.views.effective_quota", return_value=None)
    def test_unlimited_storage_shows_usage_without_a_bar(self, _quota):
        _, page = self._render()
        self.assertNotIn('x-ref="storageBar"', page)
        self.assertIn("used", page)

    @patch("workspace.core.services.activity.get_usage_stats")
    def test_mail_row_shows_the_unread_count(self, mock_stats):
        mock_stats.return_value = {
            "mail": {"total_messages": 9, "unread_messages": 2, "total_accounts": 1}
        }
        rows, page = self._render()

        self.assertEqual([row["slug"] for row in rows], ["mail"])
        self.assertIn("2 unread", page)
        self.assertIn('data-usage-count="9"', page)


# ── _build_dashboard_context ────────────────────────────────────


class BuildDashboardContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dashuser",
            email="dash@test.com",
            password="pass123",
        )

    def tearDown(self):
        cache.clear()

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_context_includes_notification_counts_on_modules(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [_mod("chat"), _mod("calendar"), _mod("dashboard")]
        mock_badges.return_value = {
            "chat": {"count": 5, "url": None},
            "calendar": {"count": 2, "url": None},
        }

        context = _build_dashboard_context(self.user)

        modules = context["modules"]
        self.assertEqual(len(modules), 2)  # dashboard excluded
        chat_mod = next(m for m in modules if m["slug"] == "chat")
        cal_mod = next(m for m in modules if m["slug"] == "calendar")
        self.assertEqual(chat_mod["notification_count"], 5)
        self.assertEqual(cal_mod["notification_count"], 2)

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_context_defaults_notification_count_to_zero(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [_mod("files")]
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)

        files_mod = context["modules"][0]
        self.assertEqual(files_mod["notification_count"], 0)

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_tile_links_to_pending_action_target(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("chat")]
        mock_badges.return_value = {
            "chat": {"count": 3, "url": "/chat/abc"},
        }

        context = _build_dashboard_context(self.user)

        self.assertEqual(context["modules"][0]["url"], "/chat/abc")

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_tile_links_to_module_home_without_target(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("chat")]
        mock_badges.return_value = {
            "chat": {"count": 3, "url": None},
        }

        context = _build_dashboard_context(self.user)

        self.assertEqual(context["modules"][0]["url"], "/chat")

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_excludes_dashboard_from_modules(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("dashboard"), _mod("mail")]
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)
        slugs = [m["slug"] for m in context["modules"]]
        self.assertNotIn("dashboard", slugs)
        self.assertIn("mail", slugs)

    @patch("workspace.dashboard.services.modules.visible_modules")
    @patch("workspace.dashboard.services.modules.get_unread_badges")
    def test_context_does_not_include_upcoming_events(self, mock_badges, mock_visible):
        """Upcoming events load async via /dashboard/upcoming - not via the
        main context."""
        mock_visible.return_value = []
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertNotIn("upcoming_events", context)

    @patch("workspace.dashboard.services.modules.visible_modules")
    @patch("workspace.dashboard.services.modules.get_unread_badges")
    def test_context_includes_usage_rows(self, mock_badges, mock_visible):
        mock_visible.return_value = []
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertIn("usage_rows", context)

    @patch("workspace.dashboard.services.modules.visible_modules")
    @patch("workspace.dashboard.services.modules.get_unread_badges")
    def test_context_includes_storage_quota(self, mock_badges, mock_visible):
        mock_visible.return_value = []
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertIn("storage_quota", context)

    @patch("workspace.dashboard.views._get_activity_context")
    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_includes_activity_when_requested(
        self, mock_visible, mock_badges, mock_activity
    ):
        mock_visible.return_value = []
        mock_badges.return_value = {}
        mock_activity.return_value = {"activity_events": []}

        context = _build_dashboard_context(self.user, include_activity=True)
        mock_activity.assert_called_once()
        self.assertIn("activity_events", context)

    @patch("workspace.dashboard.views._get_activity_context")
    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_excludes_activity_when_not_requested(
        self, mock_visible, mock_badges, mock_activity
    ):
        mock_visible.return_value = []
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user, include_activity=False)
        mock_activity.assert_not_called()
        self.assertNotIn("activity_events", context)

    @patch("workspace.dashboard.views._get_activity_context")
    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_forwards_activity_source(self, mock_visible, mock_badges, mock_activity):
        mock_visible.return_value = []
        mock_badges.return_value = {}
        mock_activity.return_value = {}

        _build_dashboard_context(self.user, activity_source="chat")
        mock_activity.assert_called_once_with(self.user, source="chat")

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_grid_uses_visible_modules_only(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("files")]
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertEqual([m["slug"] for m in context["modules"]], ["files"])

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_preview_flag_carried_into_module_context(self, mock_visible, mock_badges):
        # The home grid template renders the Preview badge off this flag, so the
        # view must carry preview through to each module dict.
        mock_visible.return_value = [_mod("files", preview=True)]
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)
        files_mod = context["modules"][0]
        self.assertTrue(files_mod["preview"])

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_module_kept_off_the_dashboard_has_no_tile_and_no_hide_toggle(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [
            _mod("files"),
            _mod("imports", show_on_dashboard=False),
        ]
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)

        self.assertEqual([m["slug"] for m in context["modules"]], ["files"])
        self.assertEqual([a["slug"] for a in context["dashboard_apps"]], ["files"])

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_hidden_modules_excluded_from_grid(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("chat"), _mod("mail"), _mod("files")]
        mock_badges.return_value = {}
        set_setting(self.user, "dashboard", "hidden_modules", ["mail"])

        context = _build_dashboard_context(self.user)

        slugs = [m["slug"] for m in context["modules"]]
        self.assertNotIn("mail", slugs)
        self.assertIn("chat", slugs)
        self.assertIn("files", slugs)

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_empty_hidden_modules_shows_all(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("chat"), _mod("mail")]
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)

        slugs = [m["slug"] for m in context["modules"]]
        self.assertEqual(set(slugs), {"chat", "mail"})

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_unknown_hidden_slug_is_ignored(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("chat")]
        mock_badges.return_value = {}
        set_setting(self.user, "dashboard", "hidden_modules", ["nonexistent"])

        context = _build_dashboard_context(self.user)

        slugs = [m["slug"] for m in context["modules"]]
        self.assertEqual(slugs, ["chat"])

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_context_includes_dashboard_apps_with_hidden_flag(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [_mod("chat"), _mod("mail"), _mod("dashboard")]
        mock_badges.return_value = {}
        set_setting(self.user, "dashboard", "hidden_modules", ["mail"])

        context = _build_dashboard_context(self.user)

        apps = {a["slug"]: a for a in context["dashboard_apps"]}
        self.assertNotIn("dashboard", apps)  # dashboard tile excluded
        self.assertFalse(apps["chat"]["hidden"])
        self.assertTrue(apps["mail"]["hidden"])
        # hidden app still appears in the popover list (unlike the grid)
        self.assertIn("mail", apps)

    @patch("workspace.dashboard.services.modules.get_unread_badges", return_value={})
    @patch("workspace.dashboard.services.modules.visible_modules", return_value=[])
    def test_temperature_unit_defaults_to_celsius(self, mock_visible, mock_badges):
        context = _build_dashboard_context(self.user)

        self.assertEqual(context["temperature_unit"], "celsius")
        self.assertEqual(
            dict(context["temperature_units"]), {"celsius": "°C", "fahrenheit": "°F"}
        )

    @patch("workspace.dashboard.services.modules.get_unread_badges", return_value={})
    @patch("workspace.dashboard.services.modules.visible_modules", return_value=[])
    def test_temperature_unit_follows_the_setting(self, mock_visible, mock_badges):
        set_setting(self.user, "dashboard", "temperature_unit", "fahrenheit")

        context = _build_dashboard_context(self.user)

        self.assertEqual(context["temperature_unit"], "fahrenheit")

    @patch("workspace.dashboard.services.modules.get_unread_badges", return_value={})
    @patch("workspace.dashboard.services.modules.visible_modules", return_value=[])
    def test_unknown_stored_temperature_unit_falls_back_to_celsius(
        self, mock_visible, mock_badges
    ):
        set_setting(self.user, "dashboard", "temperature_unit", "kelvin")

        context = _build_dashboard_context(self.user)

        self.assertEqual(context["temperature_unit"], "celsius")

    def test_weather_widget_reads_in_the_user_unit(self):
        set_setting(self.user, "dashboard", "temperature_unit", "fahrenheit")
        self.client.force_login(self.user)

        response = self.client.get("/")

        self.assertContains(response, "weatherWidget('fahrenheit')")

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_context_lists_today_sources_shown_by_default(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = []
        mock_badges.return_value = {}

        context = _build_dashboard_context(self.user)

        slugs = [source["slug"] for source in context["today_sources"]]
        self.assertIn("events", slugs)
        self.assertIn("mail", slugs)
        self.assertEqual(context["hidden_today_sources"], [])
        self.assertTrue(context["show_today"])

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_context_marks_switched_off_today_sources(self, mock_visible, mock_badges):
        mock_visible.return_value = []
        mock_badges.return_value = {}
        set_setting(self.user, "dashboard", HIDDEN_TODAY_SOURCES, ["mail"])

        context = _build_dashboard_context(self.user)

        self.assertEqual(context["hidden_today_sources"], ["mail"])
        self.assertTrue(context["show_today"])

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_context_hides_today_when_every_source_is_off(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = []
        mock_badges.return_value = {}
        every_slug = [
            source.slug for source in today_registry.visible_sources(self.user)
        ]
        set_setting(self.user, "dashboard", HIDDEN_TODAY_SOURCES, every_slug)

        context = _build_dashboard_context(self.user)

        self.assertFalse(context["show_today"])

    @override_settings(PREVIEW_VISIBILITY="none")
    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_context_leaves_out_sources_of_a_module_the_user_cannot_see(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = []
        mock_badges.return_value = {}
        with patch.dict(
            registry._modules,
            {"mail": replace(registry.get("mail"), preview=True)},
        ):
            context = _build_dashboard_context(self.user)

        slugs = [source["slug"] for source in context["today_sources"]]
        self.assertNotIn("mail", slugs)


# ── switcher_modules_for ────────────────────────────────────────


class SwitcherModulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="switcheruser",
            email="switcher@test.com",
            password="pass123",
        )

    def tearDown(self):
        cache.clear()

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_current_module_off_the_dashboard_is_appended(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [
            _mod("files"),
            _mod("imports", show_on_dashboard=False),
        ]
        mock_badges.return_value = {}

        switcher = switcher_modules_for(
            self.user, _mod("imports", show_on_dashboard=False)
        )

        imports_tile = next(m for m in switcher if m["slug"] == "imports")
        self.assertEqual(imports_tile["notification_count"], 0)
        self.assertEqual(imports_tile["url"], "/imports")

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_current_module_hidden_by_preference_is_appended(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [_mod("chat"), _mod("mail")]
        mock_badges.return_value = {}
        set_setting(self.user, "dashboard", "hidden_modules", ["mail"])

        switcher = switcher_modules_for(self.user, _mod("mail"))

        self.assertIn("mail", [m["slug"] for m in switcher])

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_appended_current_module_keeps_its_unread_count(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [_mod("chat"), _mod("mail")]
        mock_badges.return_value = {"mail": {"count": 3, "url": "/mail/one"}}
        set_setting(self.user, "dashboard", "hidden_modules", ["mail"])

        switcher = switcher_modules_for(self.user, _mod("mail"))

        mail = next(m for m in switcher if m["slug"] == "mail")
        self.assertEqual(mail["notification_count"], 3)
        self.assertEqual(mail["url"], "/mail")

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_switcher_tile_links_to_the_module_home_not_a_single_unread_item(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [_mod("chat")]
        mock_badges.return_value = {"chat": {"count": 1, "url": "/chat/abc"}}

        dashboard_grid, _ = dashboard_modules(self.user)
        self.assertEqual(dashboard_grid[0]["url"], "/chat/abc")

        switcher = switcher_modules_for(self.user, _mod("chat"))
        self.assertEqual(switcher[0]["url"], "/chat")

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_current_module_already_in_the_grid_is_not_duplicated(
        self, mock_visible, mock_badges
    ):
        mock_visible.return_value = [_mod("files")]
        mock_badges.return_value = {}

        switcher = switcher_modules_for(self.user, _mod("files"))

        self.assertEqual(len(switcher), 1)


# ── _get_activity_context ───────────────────────────────────────


class GetActivityContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="actuser",
            password="pass123",
        )

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_returns_expected_keys(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = [{"slug": "chat", "label": "Chat"}]

        ctx = _get_activity_context(self.user)

        expected_keys = {
            "activity_events",
            "activity_sources",
            "activity_source",
            "activity_search",
            "activity_has_more",
            "activity_next_offset",
            "activity_prefix",
            "activity_base_url",
        }
        self.assertEqual(set(ctx.keys()), expected_keys)

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_has_more_when_extra_events_returned(
        self, mock_events, mock_annotate, mock_sources
    ):
        # Return ACTIVITY_LIMIT+1 events to trigger has_more
        mock_events.return_value = [{"id": i} for i in range(ACTIVITY_LIMIT + 1)]
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user)

        self.assertTrue(ctx["activity_has_more"])
        self.assertEqual(len(ctx["activity_events"]), ACTIVITY_LIMIT)

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_no_more_when_fewer_events(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = [{"id": 1}]
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user)

        self.assertFalse(ctx["activity_has_more"])
        self.assertEqual(len(ctx["activity_events"]), 1)

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_offset_forwarded_to_next_offset(
        self, mock_events, mock_annotate, mock_sources
    ):
        mock_events.return_value = []
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user, offset=20)

        self.assertEqual(ctx["activity_next_offset"], 20 + ACTIVITY_LIMIT)

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_source_filter_passed_to_service(
        self, mock_events, mock_annotate, mock_sources
    ):
        mock_events.return_value = []
        mock_sources.return_value = []

        _get_activity_context(self.user, source="files")

        call_kwargs = mock_events.call_args.kwargs
        self.assertEqual(call_kwargs["source"], "files")

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_search_filter_passed_to_service(
        self, mock_events, mock_annotate, mock_sources
    ):
        mock_events.return_value = []
        mock_sources.return_value = []

        _get_activity_context(self.user, search="deploy")

        call_kwargs = mock_events.call_args.kwargs
        self.assertEqual(call_kwargs["search"], "deploy")

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_empty_search_becomes_none(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user, search=None)

        self.assertEqual(ctx["activity_search"], "")

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_annotate_time_ago_called(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = [{"id": 1}]
        mock_sources.return_value = []

        _get_activity_context(self.user)

        mock_annotate.assert_called_once()

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_excludes_current_user_events(
        self, mock_events, mock_annotate, mock_sources
    ):
        mock_events.return_value = []
        mock_sources.return_value = []

        _get_activity_context(self.user)

        call_kwargs = mock_events.call_args.kwargs
        self.assertEqual(call_kwargs["exclude_user_id"], self.user.id)

    @patch("workspace.dashboard.views.get_sources")
    @patch("workspace.dashboard.views.annotate_time_ago")
    @patch("workspace.dashboard.views.get_recent_events")
    def test_module_visibility_user_forwarded_to_service(
        self, mock_events, mock_annotate, mock_sources
    ):
        mock_events.return_value = []
        mock_sources.return_value = []

        _get_activity_context(self.user)

        self.assertEqual(mock_events.call_args.kwargs["visible_to"], self.user)
        mock_sources.assert_called_once_with(self.user)


# ── preview-module visibility in the activity feed ──────────────


class ActivityPreviewVisibilityTests(TestCase):
    """The activity source tabs must not expose modules the user cannot see
    (preview modules restricted to staff/admin). Uses the real registries,
    with the projects module forced into preview for the duration (no real
    module ships as preview right now)."""

    def setUp(self):
        mods = dict(registry._modules)
        mods["projects"] = replace(mods["projects"], preview=True)
        patcher = patch.object(registry, "_modules", mods)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.normal = User.objects.create_user(
            username="actnormal",
            password="pass123",
        )
        self.staff = User.objects.create_user(
            username="actstaff",
            password="pass123",
            is_staff=True,
        )

    def tearDown(self):
        cache.clear()

    def _source_slugs(self, resp):
        return [s["slug"] for s in resp.context["activity_sources"]]

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_preview_tab_hidden_from_normal_user_on_dashboard(self):
        self.client.login(username="actnormal", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("projects", self._source_slugs(resp))

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_preview_tab_visible_to_staff_on_dashboard(self):
        self.client.login(username="actstaff", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("projects", self._source_slugs(resp))

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_preview_tab_hidden_in_feed_partial(self):
        self.client.login(username="actnormal", password="pass123")
        resp = self.client.get(
            "/dashboard/activity",
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("projects", self._source_slugs(resp))


# ── index view ──────────────────────────────────────────────────


class IndexViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="viewuser",
            password="pass123",
        )

    def tearDown(self):
        cache.clear()

    def test_requires_login(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_returns_200_for_authenticated_user(self, mock_ctx):
        mock_ctx.return_value = {"modules": [], "usage_rows": []}
        self.client.login(username="viewuser", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_sets_activity_tab_to_all(self, mock_ctx):
        mock_ctx.return_value = {"modules": []}
        self.client.login(username="viewuser", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.context["activity_tab"], "all")

    def test_today_card_loads_after_the_page_by_default(self):
        self.client.login(username="viewuser", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="dashboard-today"')
        self.assertContains(resp, "$ajax('/dashboard/today')")

    def test_today_card_off_keeps_slot_without_loading(self):
        """Every source off: the section stays as an empty swap slot (so the
        prefs toggles can bring it back live) but must not auto-load."""
        every_slug = [
            source.slug for source in today_registry.visible_sources(self.user)
        ]
        set_setting(self.user, "dashboard", HIDDEN_TODAY_SOURCES, every_slug)
        self.client.login(username="viewuser", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="dashboard-today"')
        self.assertNotContains(resp, "$ajax('/dashboard/today')")

    @patch("workspace.dashboard.views._today_list")
    def test_does_not_query_today_sources_on_initial_render(self, mock_list):
        self.client.login(username="viewuser", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        mock_list.assert_not_called()

    @patch("workspace.dashboard.views.get_recent_events")
    def test_does_not_compute_feed_on_initial_render(self, mock_events):
        """The activity feed is deferred to an async load, so the initial
        dashboard render must not run the per-provider feed fan-out."""
        mock_events.return_value = []
        self.client.login(username="viewuser", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        mock_events.assert_not_called()

    def test_renders_async_feed_loader(self):
        """The feed area must render an empty target that fetches itself via
        alpine-ajax after paint, instead of the server-rendered feed."""
        self.client.login(username="viewuser", password="pass123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "$ajax(feedUrl, { target: 'dashboard-activity' })")


# ── modules_fragment view ───────────────────────────────────────


class ModulesFragmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="moduser",
            password="pass123",
        )

    def tearDown(self):
        cache.clear()

    def test_requires_login(self):
        resp = self.client.get("/dashboard/modules")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_excludes_hidden_modules(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("chat"), _mod("mail")]
        mock_badges.return_value = {}
        set_setting(self.user, "dashboard", "hidden_modules", ["mail"])
        self.client.login(username="moduser", password="pass123")

        resp = self.client.get("/dashboard/modules")

        self.assertEqual(resp.status_code, 200)
        slugs = [m["slug"] for m in resp.context["modules"]]
        self.assertIn("chat", slugs)
        self.assertNotIn("mail", slugs)

    @patch("workspace.dashboard.services.modules.get_unread_badges")
    @patch("workspace.dashboard.services.modules.visible_modules")
    def test_renders_swap_target_id(self, mock_visible, mock_badges):
        mock_visible.return_value = [_mod("chat")]
        mock_badges.return_value = {}
        self.client.login(username="moduser", password="pass123")

        resp = self.client.get("/dashboard/modules")

        self.assertContains(resp, 'id="dashboard-modules-grid"')


# ── activity_feed view ──────────────────────────────────────────


class ActivityFeedViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="feeduser",
            password="pass123",
        )
        self.client.login(username="feeduser", password="pass123")

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get("/dashboard/activity")
        self.assertEqual(resp.status_code, 302)

    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_full_page_without_alpine_header(self, mock_ctx):
        mock_ctx.return_value = {"modules": []}
        resp = self.client.get("/dashboard/activity")
        self.assertEqual(resp.status_code, 200)
        # Full page renders the dashboard template
        self.assertTemplateUsed(resp, "dashboard/index.html")

    @patch("workspace.dashboard.views._get_activity_context")
    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_alpine_request_returns_partial(self, mock_ctx, mock_activity):
        mock_ctx.return_value = {"modules": []}
        mock_activity.return_value = {
            "activity_events": [],
            "activity_sources": [],
            "activity_source": None,
            "activity_search": "",
            "activity_has_more": False,
            "activity_next_offset": 10,
            "activity_prefix": "dashboard-activity",
            "activity_base_url": "/dashboard/activity",
        }
        resp = self.client.get(
            "/dashboard/activity",
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "core/partials/activity_feed.html")

    @patch("workspace.dashboard.views._get_activity_context")
    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_alpine_append_uses_activity_page_template(self, mock_ctx, mock_activity):
        mock_ctx.return_value = {"modules": []}
        mock_activity.return_value = {
            "activity_events": [],
            "activity_sources": [],
            "activity_source": None,
            "activity_search": "",
            "activity_has_more": False,
            "activity_next_offset": 20,
            "activity_prefix": "dashboard-activity",
            "activity_base_url": "/dashboard/activity",
        }
        resp = self.client.get(
            "/dashboard/activity?offset=10",
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "core/partials/activity_page.html")

    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_source_param_sets_activity_tab(self, mock_ctx):
        mock_ctx.return_value = {"modules": []}
        resp = self.client.get("/dashboard/activity?source=chat")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["activity_tab"], "chat")

    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_no_source_defaults_tab_to_all(self, mock_ctx):
        mock_ctx.return_value = {"modules": []}
        resp = self.client.get("/dashboard/activity")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["activity_tab"], "all")

    @patch("workspace.dashboard.views._get_activity_context")
    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_search_param_forwarded(self, mock_ctx, mock_activity):
        mock_ctx.return_value = {"modules": []}
        mock_activity.return_value = {
            "activity_events": [],
            "activity_sources": [],
            "activity_source": None,
            "activity_search": "deploy",
            "activity_has_more": False,
            "activity_next_offset": 10,
            "activity_prefix": "dashboard-activity",
            "activity_base_url": "/dashboard/activity",
        }
        resp = self.client.get(
            "/dashboard/activity?q=deploy",
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        mock_activity.assert_called_once()
        call_kwargs = mock_activity.call_args.kwargs
        self.assertEqual(call_kwargs["search"], "deploy")

    @patch("workspace.dashboard.views._get_activity_context")
    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_alpine_request_does_not_recompute_dashboard_context(
        self, mock_ctx, mock_activity
    ):
        """The async feed fetch renders only the feed partial, so it must not
        recompute pending-action counts / usage stats via the full context."""
        mock_ctx.return_value = {"modules": []}
        mock_activity.return_value = {
            "activity_events": [],
            "activity_sources": [],
            "activity_source": None,
            "activity_search": "",
            "activity_has_more": False,
            "activity_next_offset": 10,
            "activity_prefix": "dashboard-activity",
            "activity_base_url": "/dashboard/activity",
        }
        resp = self.client.get("/dashboard/activity", HTTP_X_ALPINE_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        mock_ctx.assert_not_called()

    @patch("workspace.dashboard.views._get_activity_context")
    def test_malformed_or_negative_offset_falls_back_to_first_page(self, mock_activity):
        mock_activity.return_value = {}
        for raw in ("abc", "", "1.5", "-10"):
            with self.subTest(offset=raw):
                mock_activity.reset_mock()
                resp = self.client.get(
                    "/dashboard/activity",
                    {"offset": raw},
                    HTTP_X_ALPINE_REQUEST="true",
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(mock_activity.call_args.kwargs["offset"], 0)
                self.assertTemplateUsed(resp, "core/partials/activity_feed.html")

    @patch("workspace.dashboard.views._build_dashboard_context")
    def test_malformed_offset_on_full_page_renders(self, mock_ctx):
        mock_ctx.return_value = {"modules": []}
        resp = self.client.get("/dashboard/activity", {"offset": "abc"})
        self.assertEqual(resp.status_code, 200)


class ActivityFeedSourceParamTests(TestCase):
    """``?source=`` lands in the feed URLs and inside the Alpine ``x-data``
    expression of the full page, where the browser decodes HTML entities
    before Alpine evaluates the attribute as JavaScript."""

    PAYLOAD = "');alert(document.domain);//"

    def setUp(self):
        self.user = User.objects.create_user(username="srcuser", password="pass123")
        self.client.login(username="srcuser", password="pass123")

    def tearDown(self):
        cache.clear()

    def _activity_tab_literal(self, resp):
        match = re.search(r'x-data="\{ activityTab: (.*?), activitySearch', resp.text)
        self.assertIsNotNone(match)
        return html.unescape(match.group(1))

    def test_unknown_source_falls_back_to_all_on_full_page(self):
        resp = self.client.get("/dashboard/activity", {"source": self.PAYLOAD})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["activity_tab"], "all")
        self.assertEqual(self._activity_tab_literal(resp), "'all'")
        self.assertNotIn("alert(document.domain)", resp.text)

    @patch("workspace.dashboard.views._get_activity_context")
    def test_unknown_source_falls_back_to_all_in_feed_partial(self, mock_activity):
        mock_activity.return_value = {}
        resp = self.client.get(
            "/dashboard/activity",
            {"source": self.PAYLOAD},
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(mock_activity.call_args.kwargs["source"])

    def test_known_source_is_kept(self):
        resp = self.client.get("/dashboard/activity", {"source": "chat"})
        self.assertEqual(self._activity_tab_literal(resp), "'chat'")

    def test_tab_is_rendered_as_a_single_js_string_literal(self):
        with patch(
            "workspace.dashboard.views.allowed_activity_sources",
            return_value={self.PAYLOAD},
        ):
            resp = self.client.get("/dashboard/activity", {"source": self.PAYLOAD})
        self.assertEqual(
            self._activity_tab_literal(resp),
            "'\\u0027)\\u003Balert(document.domain)\\u003B//'",
        )


# ── today_fragment view ─────────────────────────────────────────


# {% include %} renders a Template object as is, so the test needs no file.
_ROW = Template("<span>{{ item }}{% if urgent %}!{% endif %}</span>")


def _source(slug, items=(), order=1, module_slug="chat", total=None, urgent=()):
    return TodaySource(
        slug=slug,
        module_slug=module_slug,
        label=slug.title(),
        description="",
        icon="circle",
        url=f"/{slug}",
        items_fn=lambda user, now, tz: TodayRows(
            list(items), len(items) if total is None else total
        ),
        template=_ROW,
        is_urgent=lambda item: item in urgent,
        order=order,
    )


class TodayFragmentViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="todayuser", password="pw")
        self.client.force_login(self.user)
        patcher = patch.dict(today_registry._sources, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        cache.clear()

    def _register(self, *sources):
        for source in sources:
            today_registry.register(source)

    def _rows(self, resp):
        return [(row["source"].slug, row["item"]) for row in resp.context["today_rows"]]

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get("/dashboard/today")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_every_source_lands_in_one_list_in_source_order(self):
        self._register(
            _source("inbox", items=["Contract"], order=2),
            _source("agenda", items=["Standup", "Review"], order=1),
        )

        resp = self.client.get("/dashboard/today")

        self.assertTemplateUsed(resp, "dashboard/partials/today.html")
        self.assertEqual(
            self._rows(resp),
            [("agenda", "Standup"), ("agenda", "Review"), ("inbox", "Contract")],
        )
        self.assertContains(resp, "<span>Standup</span>", html=True)
        self.assertContains(resp, 'data-today-source="inbox"')

    def test_urgent_rows_come_first_whatever_their_source(self):
        self._register(
            _source("agenda", items=["Standup"], order=1),
            _source("tasks", items=["Later", "Fire"], order=2, urgent={"Fire"}),
            _source("inbox", items=["Outage", "Hello"], order=3, urgent={"Outage"}),
        )

        resp = self.client.get("/dashboard/today")

        self.assertEqual(
            self._rows(resp),
            [
                ("tasks", "Fire"),
                ("inbox", "Outage"),
                ("agenda", "Standup"),
                ("tasks", "Later"),
                ("inbox", "Hello"),
            ],
        )
        self.assertEqual(resp.context["today_urgent"], 2)
        self.assertContains(resp, "<span>Fire!</span>", html=True)

    def test_each_source_gets_a_filter_counting_all_its_rows(self):
        self._register(
            _source("inbox", items=["a", "b"], total=12),
            _source("agenda", items=["c"], order=2),
        )

        resp = self.client.get("/dashboard/today")

        counters = re.findall(r"data-today-count\s*>(\d+)<", resp.content.decode())
        self.assertEqual(counters, ["12", "1"])
        self.assertEqual(resp.context["today_total"], 13)
        self.assertContains(resp, 'data-today-filter="all"')
        self.assertNotContains(resp, 'data-today-filter="urgent"')

    def test_a_single_source_needs_no_filter(self):
        self._register(_source("inbox", items=["a"]))

        resp = self.client.get("/dashboard/today")

        self.assertNotContains(resp, "data-today-filter")

    def test_rows_past_the_limit_start_hidden_behind_show_more(self):
        from workspace.dashboard.views import TODAY_LIMIT

        items = [f"row {i}" for i in range(TODAY_LIMIT + 3)]
        self._register(_source("inbox", items=items))

        resp = self.client.get("/dashboard/today")

        self.assertEqual(resp.context["today_more"], 3)
        self.assertEqual(resp.content.decode().count('style="display: none"'), 3)
        self.assertContains(resp, "Show 3 more")

    def test_a_source_without_rows_is_left_out(self):
        self._register(
            _source("agenda"),
            _source("inbox", items=["Contract"], order=2),
        )

        resp = self.client.get("/dashboard/today")

        self.assertEqual(self._rows(resp), [("inbox", "Contract")])
        self.assertNotContains(resp, 'data-today-source="agenda"')

    def test_nothing_anywhere_says_so(self):
        self._register(_source("agenda"))

        resp = self.client.get("/dashboard/today")

        self.assertContains(resp, "Nothing is waiting on you.")

    def test_a_switched_off_source_is_never_queried(self):
        items_fn = Mock(return_value=TodayRows(["Secret"], 1))
        self._register(
            replace(_source("inbox"), items_fn=items_fn),
            _source("agenda", items=["Standup"]),
        )
        set_setting(self.user, "dashboard", HIDDEN_TODAY_SOURCES, ["inbox"])

        resp = self.client.get("/dashboard/today")

        items_fn.assert_not_called()
        self.assertNotContains(resp, "Secret")

    @override_settings(PREVIEW_VISIBILITY="none")
    def test_a_source_of_a_module_the_user_cannot_see_is_never_queried(self):
        items_fn = Mock(return_value=TodayRows(["Secret"], 1))
        self._register(replace(_source("inbox", module_slug="mail"), items_fn=items_fn))

        with patch.dict(
            registry._modules,
            {"mail": replace(registry.get("mail"), preview=True)},
        ):
            resp = self.client.get("/dashboard/today")

        items_fn.assert_not_called()
        self.assertNotContains(resp, "Secret")

    def test_every_source_off_renders_the_empty_slot(self):
        """Switching the last source off swaps the card out: the fragment
        keeps the swap-target id but renders no content."""
        self._register(_source("inbox", items=["Contract"]))
        set_setting(self.user, "dashboard", HIDDEN_TODAY_SOURCES, ["inbox"])

        resp = self.client.get("/dashboard/today")

        self.assertContains(resp, 'id="dashboard-today"')
        self.assertNotContains(resp, "Contract")
        self.assertNotIn("today_rows", resp.context)

    def test_sources_get_the_users_local_clock(self):
        seen = {}

        def items_fn(user, now, tz):
            seen.update(user=user, tz=str(tz))
            return TodayRows([], 0)

        self._register(replace(_source("inbox"), items_fn=items_fn))
        set_setting(self.user, "core", "timezone", "Asia/Tokyo")

        self.client.get("/dashboard/today")

        self.assertEqual(seen, {"user": self.user, "tz": "Asia/Tokyo"})


@override_settings(PREVIEW_VISIBILITY="all")
class TodayCardWithEverySourceTests(TestCase):
    """The real sources, rendered through their own row templates."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        from workspace.calendar.models import Calendar, Event, EventMember
        from workspace.mail.models import MailAccount, MailFolder, MailMessage
        from workspace.projects.services.projects import create_project
        from workspace.projects.services.tasks import create_task

        self.user = User.objects.create_user(username="busy", password="pw")
        self.client.force_login(self.user)
        now = dj_timezone.now()

        calendar = Calendar.objects.create(name="Work", owner=self.user)
        self.event = Event.objects.create(
            calendar=calendar,
            owner=self.user,
            title="Design review",
            # Under way at any hour, so the test never lands on the day's edge.
            start=now - timedelta(minutes=1),
            end=now + timedelta(hours=1),
        )
        organizer = User.objects.create_user(username="organizer", password="pw")
        invite = Event.objects.create(
            calendar=Calendar.objects.create(name="Team", owner=organizer),
            owner=organizer,
            title="Quarterly planning",
            start=now + timedelta(days=2),
        )
        EventMember.objects.create(event=invite, user=self.user)

        project = create_project(self.user, name="Website")
        self.task = create_task(
            project,
            self.user,
            title="Fix the login page",
            assignees=[self.user],
            due_date=now.date() - timedelta(days=1),
        )

        account = MailAccount.objects.create(
            owner=self.user,
            email="busy@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="busy",
        )
        inbox = MailFolder.objects.create(
            account=account, name="INBOX", display_name="INBOX", folder_type="inbox"
        )
        self.message = MailMessage.objects.create(
            account=account,
            folder=inbox,
            imap_uid=1,
            subject="Contract to sign",
            from_name="Alice",
            date=now,
            is_starred=True,
        )

    def tearDown(self):
        cache.clear()

    def test_each_source_lists_its_row_and_links_to_it(self):
        resp = self.client.get("/dashboard/today")

        self.assertContains(resp, "Design review")
        self.assertContains(resp, f"/calendar?event={self.event.uuid}")
        self.assertContains(resp, "Quarterly planning")
        self.assertContains(resp, "Fix the login page")
        self.assertContains(resp, f"?task={self.task.uuid}")
        self.assertContains(resp, "Contract to sign")
        self.assertContains(resp, f"/mail?message={self.message.uuid}")
        for slug in ("events", "invitations", "tasks", "mail"):
            self.assertContains(resp, f'data-today-source="{slug}"')

    def test_the_overdue_task_leads_the_list(self):
        resp = self.client.get("/dashboard/today")

        first = resp.context["today_rows"][0]
        self.assertEqual((first["source"].slug, first["item"]), ("tasks", self.task))
        self.assertTrue(first["urgent"])


# ── query budget guard ──────────────────────────────────────────


class IndexViewQueryBudgetTests(TestCase):
    """Regression guard: ensure upcoming-events expansion never runs on the
    dashboard's critical render path.

    Post-fix baseline is 5 calendar_event queries:
      - 2 from the pending-action provider (one_off + masters)
      - 2 COUNT queries from calendar activity get_stats
      - 1 SELECT from get_recent_events (activity feed)
    Re-adding the upcoming-events expansion adds 2 more queries, pushing
    the total above the threshold of 5 and failing this test."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="budgetuser",
            password="pass123",
        )
        self.client.login(username="budgetuser", password="pass123")

    def test_index_spends_two_queries_on_the_storage_gauge(self):
        """One usage aggregate and one quota-row lookup, neither cached.

        Both are per-render costs the gauge added to a landing page, so a
        third one - a per-module or per-file resolution - must not creep in.
        """
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

        gauge_queries = [
            q["sql"]
            for q in ctx.captured_queries
            if "files_userstoragequota" in q["sql"]
            or ('SUM("files_file"."size")' in q["sql"])
        ]
        self.assertEqual(len(gauge_queries), 2, gauge_queries)

    def test_index_does_not_expand_upcoming_on_critical_path(self):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

        calendar_event_queries = [
            q["sql"]
            for q in ctx.captured_queries
            if 'FROM "calendar_event"' in q["sql"]
        ]
        # Baseline (post-fix) is 5 queries hitting the calendar_event table:
        #   - 2 from the pending-action provider (one_off + masters)
        #   - 2 COUNT queries from calendar activity get_stats
        #   - 1 SELECT from get_recent_events (activity feed)
        # Mail queries containing 'has_calendar_event' as a column name are
        # excluded by the case-sensitive FROM "calendar_event" filter.
        # Adding the upcoming-events expansion back (one_off + masters = 2 extra)
        # would push the count above 5, so this threshold catches that regression.
        self.assertLessEqual(
            len(calendar_event_queries),
            5,
            f"Dashboard index should run at most 5 calendar_event queries "
            f"(pending-action provider + activity stats only). Found "
            f"{len(calendar_event_queries)}: {calendar_event_queries}",
        )


class IndexViewTotalQueryBudgetTests(TestCase):
    """The whole render, on a cold cache - the state a first page view hits."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="totalbudget", password="pw")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_index_render_stays_within_its_query_budget(self):
        # 2 of these belong to the storage gauge (usage aggregate + quota row).
        with self.assertNumQueries(24):
            self.assertEqual(self.client.get("/").status_code, 200)


# ── French interface ────────────────────────────────────────────


class DashboardFrenchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="frenchuser", first_name="Élodie", password="pw"
        )
        set_setting(self.user, "core", "language", "fr")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_index_renders_in_french(self):
        resp = self.client.get("/")
        self.assertContains(resp, "Tableau de bord - Workspace")
        self.assertContains(resp, "Bienvenue, Élodie.")
        self.assertContains(resp, "Activité récente")
        self.assertNotContains(resp, "Recent Activity")

    def test_shared_words_come_from_the_common_catalog(self):
        resp = self.client.get("/")
        self.assertContains(resp, 'title="Fermer"')
        self.assertContains(resp, 'title="Rechercher"')

    def test_greetings_reach_the_browser_translated(self):
        resp = self.client.get("/")
        self.assertContains(resp, "Bonjour, %(name)s.")
        self.assertContains(resp, "Bonsoir, %(name)s.")

    @patch("workspace.core.services.activity.get_usage_stats")
    def test_unread_count_uses_the_french_plural(self, mock_stats):
        # French catalogs carry three plural forms: 0 and 1, exact millions,
        # and everything else, so a count of 3 reads the third one.
        mock_stats.return_value = {
            "mail": {"total_messages": 5, "unread_messages": 3, "total_accounts": 1}
        }
        resp = self.client.get("/")
        self.assertContains(resp, "3 non lus")

    def test_today_card_in_french(self):
        resp = self.client.get("/dashboard/today")
        self.assertContains(resp, "Aujourd’hui")
        self.assertContains(resp, "Rien ne vous attend.")
