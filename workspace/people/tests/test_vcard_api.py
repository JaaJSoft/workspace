import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from workspace.people.models import Person, PersonList
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person

User = get_user_model()
FIXTURES = Path(__file__).parent / "vcards"


def upload(name="contacts.vcf", content=None):
    if content is None:
        content = (FIXTURES / "google.vcf").read_bytes()
    return SimpleUploadedFile(name, content, content_type="text/vcard")


class ImportApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)
        self.client.force_authenticate(self.alice)

    def test_import_into_own_book(self):
        response = self.client.post(
            "/api/v1/people/import", {"file": upload()}, format="multipart"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"created": 2, "updated": 0, "lists": 0})
        self.assertEqual(Person.objects.filter(owner=self.alice).count(), 2)

    def test_import_into_group(self):
        response = self.client.post(
            "/api/v1/people/import",
            {"file": upload(), "group": self.team.id},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Person.objects.filter(group=self.team).count(), 2)

    def test_a_group_one_is_not_in_is_refused_like_one_that_does_not_exist(self):
        other = Group.objects.create(name="other")

        def refusal(group_id):
            response = self.client.post(
                "/api/v1/people/import",
                {"file": upload(), "group": group_id},
                format="multipart",
            )
            return response.status_code, response.content.replace(
                str(group_id).encode(), b"<id>"
            )

        absent = refusal(987654)
        self.assertEqual(absent[0], 400)
        self.assertEqual(refusal(other.id), absent)
        self.assertFalse(Person.objects.exists())

    def test_missing_file_is_400(self):
        response = self.client.post("/api/v1/people/import", {}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data)

    def test_garbage_is_400(self):
        response = self.client.post(
            "/api/v1/people/import",
            {"file": upload(content=b"hello there")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data)

    def test_too_large_is_400(self):
        from workspace.people.serializers import IMPORT_MAX_SIZE

        response = self.client.post(
            "/api/v1/people/import",
            {"file": upload(content=b"x" * (IMPORT_MAX_SIZE + 1))},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data)

    def test_latin1_file_is_decoded(self):
        content = "BEGIN:VCARD\nVERSION:3.0\nFN:Zoé\nEND:VCARD\n".encode("latin-1")
        response = self.client.post(
            "/api/v1/people/import",
            {"file": upload(content=content)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Person.objects.get().display_name, "Zoé")

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        response = self.client.post(
            "/api/v1/people/import", {"file": upload()}, format="multipart"
        )
        self.assertIn(response.status_code, (401, 403))


class ExportApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)
        self.client.force_authenticate(self.alice)
        self.jane = create_person(
            owner=self.alice,
            display_name='Jane "JD" Doe',
            emails=[{"value": "jane@example.com", "type": "work"}],
        )
        self.team_contact = create_person(group=self.team, display_name="Team Contact")
        self.bobs = create_person(owner=self.bob, display_name="Bob's friend")
        self.friends = create_list(owner=self.alice, name="Friends")
        add_members(self.friends, [self.jane])

    def test_export_everything_reachable(self):
        response = self.client.get("/api/v1/people/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/vcard; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="contacts.vcf"'
        )
        text = response.content.decode()
        self.assertIn('FN:Jane "JD" Doe', text)
        self.assertIn("FN:Team Contact", text)
        self.assertNotIn("Bob's friend", text)
        self.assertIn("KIND:group", text)
        self.assertIn(f"MEMBER:urn:uuid:{self.jane.uuid}", text)

    def test_export_one_address_book(self):
        response = self.client.get("/api/v1/people/export", {"group": "none"})
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="my-contacts.vcf"'
        )
        text = response.content.decode()
        self.assertIn("FN:Jane", text)
        self.assertNotIn("FN:Team Contact", text)
        self.assertIn("KIND:group", text)
        for params in ({"group": self.team.id}, {"group_name": "Team"}):
            with self.subTest(params=params):
                response = self.client.get("/api/v1/people/export", params)
                text = response.content.decode()
                self.assertIn("FN:Team Contact", text)
                self.assertNotIn("FN:Jane", text)
                # The lists of another address book stay out of the file.
                self.assertNotIn("KIND:group", text)
        response = self.client.get("/api/v1/people/export", {"group": self.team.id})
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="team.vcf"'
        )

    def test_export_takes_the_filters_of_the_contact_list(self):
        response = self.client.get("/api/v1/people/export", {"q": "jane"})
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn("FN:Jane", text)
        self.assertNotIn("FN:Team Contact", text)

    def test_a_group_one_is_not_in_exports_nothing(self):
        other = Group.objects.create(name="other")
        create_person(group=other, display_name="Others")
        response = self.client.get("/api/v1/people/export", {"group": other.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("BEGIN:VCARD"), 0)

    def test_malformed_filters_are_400(self):
        for name in ("group", "list"):
            with self.subTest(name):
                response = self.client.get("/api/v1/people/export", {name: "nope"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(response.data), [name])

    def test_download_one_person(self):
        response = self.client.get(f"/api/v1/people/{self.jane.uuid}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/vcard; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="Jane \\"JD\\" Doe.vcf"',
        )
        text = response.content.decode()
        self.assertEqual(text.count("BEGIN:VCARD"), 1)
        self.assertIn("EMAIL;TYPE=work:jane@example.com", text)

    def test_download_list_with_members(self):
        response = self.client.get(f"/api/v1/people/lists/{self.friends.uuid}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="Friends.vcf"'
        )
        text = response.content.decode()
        self.assertEqual(text.count("BEGIN:VCARD"), 2)
        self.assertIn("FN:Jane", text)
        self.assertIn("KIND:group", text)

    def test_a_download_out_of_reach_answers_like_one_that_does_not_exist(self):
        theirs = create_list(owner=self.bob, name="Theirs")
        for hidden, absent in (
            (
                f"/api/v1/people/{self.bobs.uuid}/download",
                f"/api/v1/people/{uuid.uuid4()}/download",
            ),
            (
                f"/api/v1/people/lists/{theirs.uuid}/download",
                f"/api/v1/people/lists/{uuid.uuid4()}/download",
            ),
        ):
            with self.subTest(path=hidden):
                answer, missing = self.client.get(hidden), self.client.get(absent)
                self.assertEqual(answer.status_code, 404)
                self.assertEqual(
                    (answer.status_code, answer.content),
                    (missing.status_code, missing.content),
                )

    def test_person_payload_carries_source_and_import_uid_read_only(self):
        response = self.client.get(f"/api/v1/people/{self.jane.uuid}")
        self.assertEqual(response.data["source"], "manual")
        self.assertEqual(response.data["import_uid"], "")
        response = self.client.patch(
            f"/api/v1/people/{self.jane.uuid}",
            {"import_uid": "forged", "source": "import"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.jane.refresh_from_db()
        self.assertEqual(self.jane.import_uid, "")
        self.assertEqual(self.jane.source, "manual")

    def test_export_action_is_offered(self):
        response = self.client.post(
            "/api/v1/people/available-actions",
            {"uuids": [str(self.jane.uuid)]},
            format="json",
        )
        ids = [a["id"] for a in response.data[str(self.jane.uuid)]]
        self.assertIn("export", ids)
        self.assertLess(ids.index("export"), ids.index("delete"))
        self.assertEqual(PersonList.objects.count(), 1)
