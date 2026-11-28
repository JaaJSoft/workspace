import uuid
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
        response = self.client.put(
            self.url,
            {
                "file": png_upload(),
                "crop_x": 0,
                "crop_y": 0,
                "crop_w": 64,
                "crop_h": 64,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(response.content, b"")
        self.person.refresh_from_db()
        self.assertTrue(self.person.has_avatar)
        person = self.client.get(f"/api/v1/people/{self.person.uuid}").data
        self.assertEqual(person["avatar_url"], self.url)

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

    def test_head_is_public(self):
        save_avatar(self.person, png_upload(), 0, 0, 64, 64)
        self.client.force_authenticate(None)
        response = self.client.head(self.url)
        self.assertEqual(response.status_code, 200)

    def test_upload_unreachable_is_404(self):
        self.client.force_authenticate(self.bob)
        response = self.client.put(
            self.url,
            {
                "file": png_upload(),
                "crop_x": 0,
                "crop_y": 0,
                "crop_w": 64,
                "crop_h": 64,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 404)
        self.person.refresh_from_db()
        self.assertFalse(self.person.has_avatar)

    def test_a_contact_out_of_reach_answers_like_one_that_does_not_exist(self):
        save_avatar(self.person, png_upload(), 0, 0, 64, 64)
        self.client.force_authenticate(self.bob)
        hidden = self.client.delete(self.url)
        absent = self.client.delete(f"/api/v1/people/{uuid.uuid4()}/avatar")
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )
        self.assertTrue(default_storage.exists(avatar_path(self.person)))

    def test_post_is_not_offered(self):
        response = self.client.post(
            self.url,
            {
                "file": png_upload(),
                "crop_x": 0,
                "crop_y": 0,
                "crop_w": 64,
                "crop_h": 64,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 405)

    def test_upload_wrong_type_is_400(self):
        text = BytesIO(b"hello")
        text.name = "a.txt"
        response = self.client.put(
            self.url,
            {"file": text, "crop_x": 0, "crop_y": 0, "crop_w": 1, "crop_h": 1},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["file"])

    def test_upload_without_a_file_is_400(self):
        response = self.client.put(
            self.url,
            {"crop_x": 0, "crop_y": 0, "crop_w": 1, "crop_h": 1},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["file"])

    def test_upload_non_finite_crop_is_400(self):
        for value in ("inf", "nan", "-inf"):
            response = self.client.put(
                self.url,
                {
                    "file": png_upload(),
                    "crop_x": 0,
                    "crop_y": 0,
                    "crop_w": value,
                    "crop_h": 64,
                },
                format="multipart",
            )
            self.assertEqual(response.status_code, 400, value)
            self.assertEqual(list(response.data), ["crop_w"])

    def test_upload_empty_crop_is_400(self):
        response = self.client.put(
            self.url,
            {"file": png_upload(), "crop_x": 0, "crop_y": 0, "crop_w": 0, "crop_h": 64},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["crop_w"])

    def test_delete_avatar(self):
        save_avatar(self.person, png_upload(), 0, 0, 64, 64)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(default_storage.exists(avatar_path(self.person)))
