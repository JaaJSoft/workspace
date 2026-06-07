from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.storage import OverwriteStorage
from workspace.mail.models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailMessage,
)

User = get_user_model()


class MailAttachmentSaveToFilesTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            imap_use_ssl=True,
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject="hi",
        )
        self.attachment = MailAttachment.objects.create(
            message=self.message,
            filename="doc.pdf",
            content_type="application/pdf",
            size=3,
            content=SimpleUploadedFile(
                "doc.pdf",
                b"pdf",
                content_type="application/pdf",
            ),
        )
        self.url = f"/api/v1/mail/attachments/{self.attachment.uuid}/save-to-files"

    def test_malformed_folder_id_returns_400(self):
        """Regression: folder_id was passed straight to File.objects.get(uuid=...)
        without validation. A non-UUID string raised ValidationError from the
        UUIDField cleaning layer, slipped past `except File.DoesNotExist:`, and
        surfaced as 500. Must validate and return 4xx.
        """
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            data={"folder_id": "not-a-uuid"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_save_to_root_succeeds(self):
        """Sanity: omitting folder_id saves the attachment at the root."""
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("file_uuid", resp.data)

    def test_save_creates_independent_copy(self):
        """The saved file must be a real copy of the attachment blob, not a
        shared reference. The destination File's storage path must differ
        from the source attachment's path - otherwise deleting the mail
        message later would orphan the saved file. Pins the FieldFile-vs-
        File _committed pitfall.
        """
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        saved = File.objects.get(uuid=resp.data["file_uuid"])
        self.assertNotEqual(saved.content.name, self.attachment.content.name)
        with saved.content.open("rb") as f:
            self.assertEqual(f.read(), b"pdf")

    def test_missing_blob_returns_404(self):
        """Regression: if the underlying attachment blob has gone missing,
        attachment.content.open('rb') raises FileNotFoundError/OSError.
        Used to escape the existing `except MailAttachment.DoesNotExist:`
        and surface as 500. Must mirror the chat save-to-files 404.
        """
        self.client.force_authenticate(self.user)
        with patch.object(
            default_storage,
            "open",
            side_effect=FileNotFoundError("gone"),
        ):
            resp = self.client.post(self.url, data={}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_destination_save_failure_is_not_404(self):
        """Regression: a destination-side OSError (disk full on the dest
        path, perm denied, remote storage flake) used to be lumped under
        the source-blob 404 because the try/except wrapped both
        attachment.content.open() AND FileService.create_file. The view
        must only translate FileNotFoundError from the source open into
        404; destination-side errors propagate (5xx via middleware).

        Calls the view's post() directly with a manually-built request
        rather than the test client: a destination OSError aborts the
        request transaction and causes a TransactionManagementError
        cascade through the test client's renderers. The direct call
        exercises the same view code with the same patched failure mode
        without that infrastructure noise.
        """
        from rest_framework.test import APIRequestFactory, force_authenticate

        from workspace.mail.views_attachments import MailAttachmentSaveToFilesView

        factory = APIRequestFactory()
        request = factory.post(self.url, data={}, format="json")
        force_authenticate(request, user=self.user)
        view = MailAttachmentSaveToFilesView.as_view()

        with patch.object(
            OverwriteStorage,
            "_save",
            side_effect=OSError("disk full"),
        ):
            try:
                resp = view(request, uuid=self.attachment.uuid)
            except OSError:
                return  # OK: error propagated rather than mistranslated.
        self.assertNotEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
