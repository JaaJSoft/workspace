from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings

from workspace.core.module_registry import CommandInfo, ModuleInfo
from workspace.core.services.module_visibility import (
    filter_visible_commands,
    is_module_slug_visible,
    user_can_see_module,
    visible_modules,
)

User = get_user_model()


def _module(slug="x", preview=False, active=True):
    return ModuleInfo(
        name=slug.title(),
        slug=slug,
        description="",
        icon="i",
        color="c",
        url=f"/{slug}",
        active=active,
        preview=preview,
    )


class UserCanSeeModuleTests(TestCase):
    def setUp(self):
        self.normal = User.objects.create_user(username="n", password="x")
        self.staff = User.objects.create_user(username="s", password="x", is_staff=True)
        self.admin = User.objects.create_superuser(username="a", password="x")

    def test_non_preview_visible_to_everyone(self):
        m = _module(preview=False)
        for u in (self.normal, self.staff, self.admin, AnonymousUser()):
            self.assertTrue(user_can_see_module(u, m))

    @override_settings(PREVIEW_VISIBILITY="all")
    def test_preview_all(self):
        m = _module(preview=True)
        self.assertTrue(user_can_see_module(self.normal, m))
        self.assertTrue(user_can_see_module(self.staff, m))
        self.assertTrue(user_can_see_module(self.admin, m))

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_preview_staff(self):
        m = _module(preview=True)
        self.assertFalse(user_can_see_module(self.normal, m))
        self.assertTrue(user_can_see_module(self.staff, m))
        self.assertTrue(user_can_see_module(self.admin, m))

    @override_settings(PREVIEW_VISIBILITY="admin")
    def test_preview_admin(self):
        m = _module(preview=True)
        self.assertFalse(user_can_see_module(self.normal, m))
        self.assertFalse(user_can_see_module(self.staff, m))
        self.assertTrue(user_can_see_module(self.admin, m))

    @override_settings(PREVIEW_VISIBILITY="none")
    def test_preview_none_hidden_from_all(self):
        m = _module(preview=True)
        self.assertFalse(user_can_see_module(self.normal, m))
        self.assertFalse(user_can_see_module(self.staff, m))
        self.assertFalse(user_can_see_module(self.admin, m))

    @override_settings(PREVIEW_VISIBILITY="garbage")
    def test_invalid_setting_falls_back_to_staff(self):
        m = _module(preview=True)
        self.assertFalse(user_can_see_module(self.normal, m))
        self.assertTrue(user_can_see_module(self.staff, m))


class VisibleModulesTests(TestCase):
    def setUp(self):
        self.normal = User.objects.create_user(username="n", password="x")

    @override_settings(PREVIEW_VISIBILITY="staff")
    @patch("workspace.core.services.module_visibility.registry")
    def test_visible_modules_filters_preview_and_keeps_order(self, mock_registry):
        mods = [_module("files"), _module("lab", preview=True), _module("mail")]
        mock_registry.get_active.return_value = mods
        result = visible_modules(self.normal)
        self.assertEqual([m.slug for m in result], ["files", "mail"])

    @override_settings(PREVIEW_VISIBILITY="staff")
    @patch("workspace.core.services.module_visibility.registry")
    def test_is_module_slug_visible(self, mock_registry):
        def fake_get(slug):
            return {"files": _module("files"), "lab": _module("lab", preview=True)}.get(
                slug
            )

        mock_registry.get.side_effect = fake_get
        self.assertTrue(is_module_slug_visible(self.normal, "files"))
        self.assertFalse(is_module_slug_visible(self.normal, "lab"))
        # unknown slug -> treated as visible (not a registered module)
        self.assertTrue(is_module_slug_visible(self.normal, "ghost"))

    @override_settings(PREVIEW_VISIBILITY="staff")
    @patch("workspace.core.services.module_visibility.registry")
    def test_filter_visible_commands(self, mock_registry):
        def fake_get(slug):
            return {"files": _module("files"), "lab": _module("lab", preview=True)}.get(
                slug
            )

        mock_registry.get.side_effect = fake_get
        cmds = [
            CommandInfo(
                name="Files",
                keywords=[],
                icon="i",
                color="c",
                url="/files",
                kind="navigate",
                module_slug="files",
            ),
            CommandInfo(
                name="Lab",
                keywords=[],
                icon="i",
                color="c",
                url="/lab",
                kind="navigate",
                module_slug="lab",
            ),
        ]
        result = filter_visible_commands(self.normal, cmds)
        self.assertEqual([c.module_slug for c in result], ["files"])
