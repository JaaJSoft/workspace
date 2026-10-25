"""The files module speaks the reader's language: pages, action labels and
the notifications other people's actions send them."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShare
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()


def _make_file(owner, name="doc.txt"):
    return File.objects.create(
        owner=owner,
        name=name,
        node_type=File.NodeType.FILE,
        mime_type="text/plain",
        content=ContentFile(b"Hello", name=name),
        size=5,
    )


class FilesPageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_files_page_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/files")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mes fichiers")
        self.assertNotContains(response, "My Files")

    def test_files_page_stays_english_by_default(self):
        response = self.client.get("/files")
        self.assertContains(response, "My Files")

    def test_trash_empty_state_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/files/trash")
        self.assertContains(response, "La corbeille est vide")


class ActionLabelLanguageTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dominique", password="pass")
        self.file = _make_file(self.user)
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _delete_label(self):
        response = self.client.post(
            "/api/v1/files/actions", {"uuids": [str(self.file.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        actions = [
            payload["actions"][key] for key in payload["files"][str(self.file.uuid)]
        ]
        return next(a["label"] for a in actions if a["id"] == "delete")

    def test_action_labels_follow_the_user_language(self):
        set_setting(self.user, "core", "language", "fr")
        self.assertEqual(self._delete_label(), "Supprimer")

    def test_action_labels_default_to_english(self):
        self.assertEqual(self._delete_label(), "Delete")


class NotificationLanguageTests(APITestCase):
    """A notification is stored as text: it must be written in the language of
    whoever reads it, not of whoever triggered it."""

    def setUp(self):
        self.actor = User.objects.create_user(username="actor", password="pass")
        self.reader_fr = User.objects.create_user(username="reader_fr", password="pass")
        self.reader_en = User.objects.create_user(username="reader_en", password="pass")
        set_setting(self.reader_fr, "core", "language", "fr")
        self.file = _make_file(self.actor)
        self.client.force_authenticate(user=self.actor)

    def tearDown(self):
        cache.clear()

    def _title_for(self, user):
        return Notification.objects.get(recipient=user).title

    def test_share_notification_is_in_the_recipient_language(self):
        set_setting(self.actor, "core", "language", "en")
        response = self.client.post(
            f"/api/v1/files/{self.file.uuid}/share",
            {"shared_with": self.reader_fr.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            self._title_for(self.reader_fr),
            "actor a partagé «\xa0doc.txt\xa0» avec vous",
        )

    def test_french_actor_notifies_an_english_reader_in_english(self):
        set_setting(self.actor, "core", "language", "fr")
        self.client.force_login(self.actor)
        response = self.client.post(
            f"/api/v1/files/{self.file.uuid}/share",
            {"shared_with": self.reader_en.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            self._title_for(self.reader_en), 'actor shared "doc.txt" with you'
        )

    def test_one_fan_out_writes_each_reader_in_their_own_language(self):
        for reader in (self.reader_fr, self.reader_en):
            FileShare.objects.create(
                file=self.file, shared_by=self.actor, shared_with=reader
            )
        response = self.client.delete(f"/api/v1/files/{self.file.uuid}")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self._title_for(self.reader_fr), "actor a supprimé «\xa0doc.txt\xa0»"
        )
        self.assertEqual(self._title_for(self.reader_en), 'actor deleted "doc.txt"')
