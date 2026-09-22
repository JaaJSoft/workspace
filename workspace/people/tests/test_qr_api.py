import uuid

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from workspace.people.services.persons import create_person

User = get_user_model()
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class QRCodeApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.client.force_authenticate(self.alice)
        self.person = create_person(
            owner=self.alice,
            display_name="Alice Martin",
            emails=[{"value": "alice@acme.test", "type": "work"}],
        )
        self.bobs = create_person(owner=self.bob, display_name="Bobs")

    def get(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(f"/api/v1/people/qrcode?{query}")

    def test_svg_by_default(self):
        response = self.get(person=self.person.uuid)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", response.content)

    def test_png_on_request(self):
        response = self.get(person=self.person.uuid, kind="png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(PNG_MAGIC))

    def test_named_after_the_contact_and_shown_inline(self):
        response = self.get(person=self.person.uuid)
        self.assertEqual(
            response["Content-Disposition"], 'inline; filename="Alice Martin.svg"'
        )

    def test_never_stored_by_the_browser(self):
        response = self.get(person=self.person.uuid)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_someone_elses_contact_is_404(self):
        self.assertEqual(self.get(person=self.bobs.uuid).status_code, 404)

    def test_unknown_contact_is_404(self):
        self.assertEqual(self.get(person=uuid.uuid4()).status_code, 404)

    def test_malformed_uuid_is_400(self):
        response = self.get(person="not-a-uuid")
        self.assertEqual(response.status_code, 400)
        self.assertIn("person", response.data)

    def test_no_contact_is_400(self):
        response = self.client.get("/api/v1/people/qrcode")
        self.assertEqual(response.status_code, 400)
        self.assertIn("person", response.data)

    def test_unknown_kind_is_400(self):
        response = self.get(person=self.person.uuid, kind="webp")
        self.assertEqual(response.status_code, 400)
        self.assertIn("kind", response.data)

    def test_contact_past_what_a_qr_holds_is_413(self):
        big = create_person(
            owner=self.alice,
            display_name="Big",
            phones=[
                {"value": f"+336123456{index:02d}", "type": "cell"}
                for index in range(200)
            ],
        )
        response = self.get(person=big.uuid)
        self.assertEqual(response.status_code, 413)
        self.assertIn("person", response.data)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertIn(self.get(person=self.person.uuid).status_code, (401, 403))
