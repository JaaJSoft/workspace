from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from PIL import Image
from rest_framework.test import APITestCase

from workspace.people.services.avatar import avatar_path, save_avatar
from workspace.people.services.persons import create_person

User = get_user_model()


def png_upload(name="a.png"):
    buf = BytesIO()
    Image.new("RGB", (64, 64), "blue").save(buf, format="PNG")
    buf.seek(0)
    buf.name = name
    return buf


class PersonAvatarApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.person = create_person(owner=self.alice, display_name="Bob")
        self.url = f"/api/v1/people/{self.person.uuid}/avatar"
        self.client.force_authenticate(self.alice)

    def test_upload_then_retrieve_with_etag(self):
        response = self.client.post(
            self.url,
            {
                "image": png_upload(),
                "crop_x": 0,
                "crop_y": 0,
                "crop_w": 64,
                "crop_h": 64,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.person.refresh_from_db()
        self.assertTrue(self.person.has_avatar)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/webp")
        etag = response["ETag"]
        response = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(response.status_code, 304)

    def test_retrieve_missing_is_404(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_retrieve_is_public(self):
        save_avatar(self.person, png_upload(), 0, 0, 64, 64)
        self.client.force_authenticate(None)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_upload_unreachable_is_404(self):
        self.client.force_authenticate(self.bob)
        response = self.client.post(
            self.url,
            {
                "image": png_upload(),
                "crop_x": 0,
                "crop_y": 0,
                "crop_w": 64,
                "crop_h": 64,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_wrong_type_is_400(self):
        text = BytesIO(b"hello")
        text.name = "a.txt"
        response = self.client.post(
            self.url,
            {"image": text, "crop_x": 0, "crop_y": 0, "crop_w": 1, "crop_h": 1},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_avatar(self):
        save_avatar(self.person, png_upload(), 0, 0, 64, 64)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(default_storage.exists(avatar_path(self.person)))
