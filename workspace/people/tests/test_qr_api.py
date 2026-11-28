import uuid

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from workspace.common.tests.qr import read_qr
from workspace.people.services.persons import create_person
from workspace.people.services.vcard import person_to_qr_vcard

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

    def get(self, person, **params):
        return self.client.get(f"/api/v1/people/{person}/qrcode", params)

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

    def test_scanning_the_code_gives_the_contacts_card(self):
        response = self.get(person=self.person.uuid, kind="png")
        self.assertEqual(read_qr(response.content), person_to_qr_vcard(self.person))

    def test_named_after_the_contact_and_shown_inline(self):
        response = self.get(person=self.person.uuid)
        self.assertEqual(
            response["Content-Disposition"], 'inline; filename="Alice Martin.svg"'
        )

    def test_never_stored_by_the_browser(self):
        response = self.get(person=self.person.uuid)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_someone_elses_contact_answers_like_one_that_does_not_exist(self):
        hidden = self.get(person=self.bobs.uuid)
        absent = self.get(person=uuid.uuid4())
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_unknown_kind_is_400(self):
        response = self.get(person=self.person.uuid, kind="webp")
        self.assertEqual(response.status_code, 400)
        self.assertIn("kind", response.data)

    def test_contact_past_what_a_qr_holds_is_a_conflict(self):
        big = create_person(
            owner=self.alice,
            display_name="Big",
            phones=[
                {"value": f"+336123456{index:02d}", "type": "cell"}
                for index in range(200)
            ],
        )
        response = self.get(person=big.uuid)
        self.assertEqual(response.status_code, 409)
        self.assertIn("detail", response.data)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertIn(self.get(person=self.person.uuid).status_code, (401, 403))
