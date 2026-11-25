from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from workspace.common.services.image import SOURCE_TYPES

User = get_user_model()


class AvatarTypesTest(TestCase):
    """The picker and the upload endpoint accept the same picture types."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_login(self.user)

    def test_settings_picker_offers_the_accepted_types(self):
        response = self.client.get("/users/settings")
        self.assertContains(response, f'accept="{",".join(SOURCE_TYPES)}"')

    def test_upload_refuses_a_type_outside_the_list(self):
        image = SimpleUploadedFile("pic.bmp", b"BM", content_type="image/bmp")
        response = self.client.post(
            "/api/v1/users/me/avatar",
            {"image": image, "crop_x": 0, "crop_y": 0, "crop_w": 1, "crop_h": 1},
        )
        self.assertEqual(response.status_code, 400)
