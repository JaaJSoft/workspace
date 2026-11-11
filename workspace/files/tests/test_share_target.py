"""The manifest's share_target, and the server-side landing behind it.

The service worker answers the share itself (core/tests/js/sw_share_target
.test.js); the server only sees it when no worker ran.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from workspace.files.models import File

User = get_user_model()

MANIFEST = Path(__file__).resolve().parents[2] / "core" / "static" / "manifest.json"


class ShareTargetManifestTests(TestCase):
    def test_the_manifest_posts_shared_files_to_the_share_target_view(self):
        share_target = json.loads(MANIFEST.read_text(encoding="utf-8"))["share_target"]

        self.assertEqual(share_target["action"], reverse("files_ui:share_target"))
        self.assertEqual(share_target["method"], "POST")
        self.assertEqual(share_target["enctype"], "multipart/form-data")
        self.assertEqual(
            [f["name"] for f in share_target["params"]["files"]], ["files"]
        )


class ShareTargetFallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sharer", password="pass")
        # A share comes from the operating system: it carries no CSRF token.
        self.client = Client(enforce_csrf_checks=True)

    def test_a_share_without_a_service_worker_lands_on_the_files_page(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("files_ui:share_target"),
            {"files": SimpleUploadedFile("a.txt", b"hello")},
        )

        self.assertRedirects(
            response,
            reverse("files_ui:index") + "?share_target=",
            fetch_redirect_response=False,
        )
        self.assertFalse(File.objects.filter(owner=self.user).exists())

    def test_an_anonymous_share_goes_through_the_login_of_the_files_page(self):
        response = self.client.post(reverse("files_ui:share_target"))

        self.assertRedirects(
            response,
            reverse("files_ui:index") + "?share_target=",
            fetch_redirect_response=False,
        )
