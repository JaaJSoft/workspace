from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.photos.services.import_folder import (
    ensure_import_folder,
    import_folder,
)
from workspace.users.services.settings import get_setting, set_setting

User = get_user_model()

API = "/api/v1/photos/import-folder"


class ImportFolderTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _root_pictures_folders(self):
        return File.objects.filter(
            owner=self.user,
            parent__isnull=True,
            name="Pictures",
            node_type=File.NodeType.FOLDER,
            deleted_at__isnull=True,
        )


class ImportFolderServiceTests(ImportFolderTestCase):
    def test_reading_it_never_creates_the_default_folder(self):
        self.assertIsNone(import_folder(self.user))

        self.assertFalse(self._root_pictures_folders().exists())

    def test_ensuring_it_creates_the_default_folder_once(self):
        first = ensure_import_folder(self.user)
        second = ensure_import_folder(self.user)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self._root_pictures_folders().count(), 1)
        self.assertEqual(
            get_setting(self.user, "photos", "import_folder"), str(first.pk)
        )

    def test_an_existing_pictures_folder_is_reused(self):
        existing = FileService.create_folder(self.user, "Pictures")

        self.assertEqual(ensure_import_folder(self.user).pk, existing.pk)
        self.assertEqual(self._root_pictures_folders().count(), 1)

    def test_a_trashed_chosen_folder_falls_back_to_the_default_one(self):
        chosen = FileService.create_folder(self.user, "Camera")
        set_setting(self.user, "photos", "import_folder", str(chosen.pk))
        FileService.soft_delete(chosen, acting_user=self.user)

        self.assertIsNone(import_folder(self.user))
        self.assertEqual(ensure_import_folder(self.user).name, "Pictures")

    def test_a_malformed_stored_value_falls_back_to_the_default_one(self):
        set_setting(self.user, "photos", "import_folder", "not-a-uuid")

        self.assertIsNone(import_folder(self.user))


class ImportFolderApiTests(ImportFolderTestCase):
    def test_login_required(self):
        self.client.logout()

        self.assertIn(self.client.get(API).status_code, (401, 403))

    def test_get_names_the_default_folder_without_creating_it(self):
        response = self.client.get(API)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"uuid": None, "name": "Pictures", "path": "Pictures", "group": None},
        )
        self.assertFalse(self._root_pictures_folders().exists())

    def test_post_creates_the_default_folder(self):
        response = self.client.post(API)

        self.assertEqual(response.status_code, 200)
        folder = self._root_pictures_folders().get()
        self.assertEqual(response.json()["uuid"], str(folder.pk))

    def test_put_chooses_a_personal_folder(self):
        camera = FileService.create_folder(self.user, "Camera")
        roll = FileService.create_folder(self.user, "Roll", parent=camera)

        response = self.client.put(
            API, {"folder": str(roll.pk)}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["path"], "Camera/Roll")
        self.assertEqual(self.client.post(API).json()["uuid"], str(roll.pk))
        self.assertFalse(self._root_pictures_folders().exists())

    def test_put_chooses_a_folder_of_one_of_the_users_groups(self):
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        shared = FileService.create_folder(self.bob, "Holidays", group=family)

        response = self.client.put(
            API, {"folder": str(shared.pk)}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group"], "Family")

    def test_put_refuses_a_folder_the_user_cannot_add_files_to(self):
        others = FileService.create_folder(self.bob, "Private")
        a_file = FileService.create_file(
            self.user, "note.txt", content=ContentFile(b"x", name="note.txt")
        )

        for value in (str(others.pk), str(a_file.pk), "not-a-uuid", None):
            with self.subTest(value=value):
                response = self.client.put(
                    API, {"folder": value}, content_type="application/json"
                )

                self.assertEqual(response.status_code, 400)
        self.assertIsNone(get_setting(self.user, "photos", "import_folder"))


class ImportFolderPageTests(ImportFolderTestCase):
    def test_the_page_embeds_the_import_folder_without_creating_it(self):
        response = self.client.get("/photos")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["import_folder"]["path"], "Pictures")
        self.assertContains(response, 'id="photos-import-folder-data"')
        self.assertFalse(self._root_pictures_folders().exists())
