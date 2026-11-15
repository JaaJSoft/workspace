"""The imports module speaks its owner's language: the page, the API's error
messages, and what a job writes from a worker - its stored error and the
end-of-import notification - whatever language was active when it ran."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from workspace.imports.importers.base import Outcome
from workspace.imports.models import ImportConnection, ImportJob
from workspace.imports.services import jobs as svc
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

from .fakes import fake_provider

User = get_user_model()


class ImportsPageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_page_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/imports")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nouvelle importation")
        self.assertContains(response, "Connexions")
        self.assertNotContains(response, "New import")

    def test_page_stays_english_by_default(self):
        response = self.client.get("/imports")
        self.assertContains(response, "New import")
        self.assertNotContains(response, "Nouvelle importation")


@override_settings(IMPORTS_ALLOWED_HOSTS=["x"])
class JobLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="p")
        self.provider = fake_provider()
        self.conn = ImportConnection.objects.create(
            owner=self.user,
            provider="fake",
            label="Cloud",
            base_url="https://x/dav",
            username="a",
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _run(self):
        job = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], options={"files": {}}
        )
        svc.run_job(job.pk)
        job.refresh_from_db()
        return job

    def test_the_end_of_import_notification_is_in_the_owners_language(self):
        set_setting(self.user, "core", "language", "fr")
        self.assertEqual(self._run().status, ImportJob.Status.COMPLETED)
        notification = Notification.objects.get(recipient=self.user)
        self.assertEqual(notification.title, "Importation depuis Cloud terminée")
        self.assertIn("3 fichiers", notification.body)

    def test_the_notification_stays_english_by_default(self):
        self._run()
        notification = Notification.objects.get(recipient=self.user)
        self.assertEqual(notification.title, "Import from Cloud finished")
        self.assertIn("3 files", notification.body)

    def test_a_failure_is_stored_in_the_owners_language(self):
        set_setting(self.user, "core", "language", "fr")
        with patch(
            "workspace.imports.services.jobs._run_slice",
            side_effect=RuntimeError("boom"),
        ):
            job = ImportJob.objects.create(
                connection=self.conn, kinds=["files"], options={"files": {}}
            )
            self.assertIs(svc.run_job(job.pk), Outcome.FAILED)
        job.refresh_from_db()
        self.assertEqual(
            job.error, "Erreur inattendue, consultez les journaux du serveur."
        )
        title = Notification.objects.get(recipient=self.user).title
        self.assertEqual(title, "Échec de l’importation depuis Cloud")

    def test_an_entry_report_is_stored_in_the_owners_language(self):
        set_setting(self.user, "core", "language", "fr")
        self.provider.fail_list.add("/")
        job = self._run()
        self.assertEqual(job.status, ImportJob.Status.FAILED)
        self.assertTrue(job.error.startswith("Impossible de lire « / » : "))


@override_settings(IMPORTS_ALLOWED_HOSTS=["x"])
class ApiMessageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="p")
        fake_provider()
        self.conn = ImportConnection.objects.create(
            owner=self.user,
            provider="fake",
            label="Cloud",
            base_url="https://x/dav",
            username="a",
        )
        # force_login, not force_authenticate: the language is activated by
        # the middleware, which runs before DRF authenticates.
        self.client.force_login(self.user)
        set_setting(self.user, "core", "language", "fr")

    def tearDown(self):
        cache.clear()

    def test_a_refused_retry_answers_in_the_users_language(self):
        job = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], options={"files": {}}
        )
        response = self.client.post(f"/api/v1/imports/jobs/{job.pk}/retry")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Seule une importation échouée ou annulée peut être relancée.",
        )
