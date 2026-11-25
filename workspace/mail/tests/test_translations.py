"""The mail module speaks the reader's language: the page, the folder types,
the API messages its dialogs show, and the notifications written outside a
request."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone, translation

from workspace.mail.models import MailAccount, MailAttachment, MailFolder, MailMessage
from workspace.mail.services.notifications import notify_new_messages
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()


class MailPageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_page_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/mail")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucun compte de messagerie configuré")
        self.assertContains(response, "Nouveau message")
        self.assertNotContains(response, "No mail accounts configured")

    def test_page_stays_english_by_default(self):
        response = self.client.get("/mail")
        self.assertContains(response, "No mail accounts configured")


class MailFolderLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dominique", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="d@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="d@example.test",
        )
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_folder_type_label_is_translated(self):
        folder = MailFolder(account=self.account, name="INBOX", folder_type="inbox")
        with translation.override("fr"):
            self.assertEqual(folder.get_folder_type_display(), "Boîte de réception")
        self.assertEqual(folder.get_folder_type_display(), "Inbox")

    def _hide_inbox(self):
        inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        return self.client.patch(
            f"/api/v1/mail/folders/{inbox.uuid}",
            {"is_hidden": True},
            content_type="application/json",
        )

    def test_api_refusal_is_translated(self):
        set_setting(self.user, "core", "language", "fr")
        response = self._hide_inbox()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"], "Impossible de masquer un dossier spécial"
        )

    def test_api_refusal_defaults_to_english(self):
        response = self._hide_inbox()
        self.assertEqual(response.json()["detail"], "Cannot hide a special folder")


class MailNotificationLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sacha", password="pass")
        account = MailAccount.objects.create(
            owner=self.user,
            email="s@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="s@example.test",
        )
        self.inbox = MailFolder.objects.create(
            account=account, name="INBOX", display_name="Inbox", folder_type="inbox"
        )
        self.message = MailMessage.objects.create(
            account=account,
            folder=self.inbox,
            imap_uid=1,
            subject="",
            from_name="",
            from_email="",
            date=timezone.now(),
        )
        set_setting(self.user, "mail", "notify_mode", "all")

    def tearDown(self):
        cache.clear()

    def _notification(self):
        notify_new_messages(self.inbox, [self.message.uuid], was_initial_sync=False)
        return Notification.objects.get(recipient=self.user)

    def test_notification_is_written_in_the_recipient_language(self):
        set_setting(self.user, "core", "language", "fr")
        notification = self._notification()
        self.assertEqual(notification.title, "Nouvel e-mail")
        self.assertEqual(notification.body, "(sans objet)")

    def test_notification_defaults_to_english(self):
        notification = self._notification()
        self.assertEqual(notification.title, "New email")
        self.assertEqual(notification.body, "(no subject)")


class MailQueryParameterRefusalLanguageTests(TestCase):
    """A request the mail API refuses for a missing or malformed parameter
    answers in the reader's language, like every other refusal."""

    def setUp(self):
        self.user = User.objects.create_user(username="alix", password="pass")
        self.client.force_login(self.user)
        set_setting(self.user, "core", "language", "fr")

    def tearDown(self):
        cache.clear()

    def assertRefusedWith(self, url, detail):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400, url)
        self.assertEqual(response.json()["detail"], detail, url)

    def test_missing_account_is_refused_in_french(self):
        for url in (
            "/api/v1/mail/folders",
            "/api/v1/mail/labels",
            "/api/v1/mail/rules",
        ):
            self.assertRefusedWith(url, "Le paramètre account est obligatoire.")

    def test_malformed_uuid_is_refused_in_french(self):
        for url, parameter in (
            ("/api/v1/mail/folders?account=nope", "account"),
            ("/api/v1/mail/rules?account=nope", "account"),
            ("/api/v1/mail/messages?folder=nope", "folder"),
            ("/api/v1/mail/messages?label=nope", "label"),
            ("/api/v1/mail/contacts/autocomplete?q=ab&account_id=nope", "account_id"),
        ):
            self.assertRefusedWith(url, f"« {parameter} » doit être un UUID valide.")

    def test_missing_message_scope_is_refused_in_french(self):
        self.assertRefusedWith(
            "/api/v1/mail/messages",
            "Un paramètre folder, label ou inbox=all est obligatoire.",
        )

    def test_unknown_oauth_provider_is_refused_in_french(self):
        response = self.client.get("/api/v1/mail/oauth2/authorize?provider=nope")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), "Fournisseur inconnu")

    def test_malformed_save_to_files_folder_is_refused_in_french(self):
        account = MailAccount.objects.create(
            owner=self.user,
            email="a@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="a@example.test",
        )
        folder = MailFolder.objects.create(account=account, name="INBOX")
        message = MailMessage.objects.create(account=account, folder=folder, imap_uid=1)
        attachment = MailAttachment.objects.create(
            message=message,
            filename="doc.pdf",
            size=3,
            content=SimpleUploadedFile("doc.pdf", b"pdf"),
        )
        response = self.client.post(
            f"/api/v1/mail/attachments/{attachment.uuid}/save-to-files",
            {"folder_id": "nope"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"], "« folder_id » doit être un UUID valide."
        )
