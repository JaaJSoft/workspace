from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.actions import ActionRegistry
from workspace.files.services import FilePermission

from .test_actions import _make_file, _make_folder

MANAGE = FilePermission.MANAGE
WRITE = FilePermission.WRITE
VIEW = FilePermission.VIEW

User = get_user_model()


class ToggleFavoriteActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("toggle_favorite")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get("toggle_favorite")
        self.assertTrue(action.is_available(self.user, folder, permission=MANAGE))

    def test_shared_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("toggle_favorite")
        self.assertTrue(action.is_available(self.other, f, permission=VIEW))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("toggle_favorite")
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_dynamic_label_not_favorite(self):
        f = _make_file(self.user)
        f.is_favorite = False
        action = ActionRegistry.get("toggle_favorite")
        self.assertEqual(action.get_label(f), "Add to favorites")

    def test_dynamic_label_is_favorite(self):
        f = _make_file(self.user)
        f.is_favorite = True
        action = ActionRegistry.get("toggle_favorite")
        self.assertEqual(action.get_label(f), "Remove from favorites")

    def test_serialize_includes_state(self):
        f = _make_file(self.user)
        f.is_favorite = True
        action = ActionRegistry.get("toggle_favorite")
        data = action.serialize(f)
        self.assertIn("state", data)
        self.assertTrue(data["state"]["is_favorite"])


class TogglePinActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get("toggle_pin")
        self.assertTrue(action.is_available(self.user, folder, permission=MANAGE))

    def test_not_owner(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get("toggle_pin")
        self.assertFalse(action.is_available(self.other, folder, permission=VIEW))

    def test_deleted(self):
        folder = _make_folder(self.user)
        folder.deleted_at = timezone.now()
        action = ActionRegistry.get("toggle_pin")
        self.assertFalse(action.is_available(self.user, folder, permission=MANAGE))

    def test_dynamic_label_not_pinned(self):
        folder = _make_folder(self.user)
        folder.is_pinned = False
        action = ActionRegistry.get("toggle_pin")
        self.assertEqual(action.get_label(folder), "Pin to sidebar")

    def test_dynamic_label_is_pinned(self):
        folder = _make_folder(self.user)
        folder.is_pinned = True
        action = ActionRegistry.get("toggle_pin")
        self.assertEqual(action.get_label(folder), "Unpin from sidebar")

    def test_serialize_includes_state(self):
        folder = _make_folder(self.user)
        folder.is_pinned = True
        action = ActionRegistry.get("toggle_pin")
        data = action.serialize(folder)
        self.assertIn("state", data)
        self.assertTrue(data["state"]["is_pinned"])


class ShareActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("share")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("share")
        self.assertFalse(action.is_available(self.other, f, permission=WRITE))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get("share")
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))
