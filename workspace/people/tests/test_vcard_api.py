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
            {"file": upload(), "scope": f"group:{self.team.id}"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Person.objects.filter(group=self.team).count(), 2)

    def test_foreign_group_is_400(self):
        other = Group.objects.create(name="other")
        response = self.client.post(
            "/api/v1/people/import",
            {"file": upload(), "scope": f"group:{other.id}"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
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
        from workspace.people.views.vcard import IMPORT_MAX_SIZE

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

    def test_export_one_scope(self):
        response = self.client.get("/api/v1/people/export", {"scope": "mine"})
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="my-contacts.vcf"'
        )
        text = response.content.decode()
        self.assertIn("FN:Jane", text)
        self.assertNotIn("FN:Team Contact", text)
        response = self.client.get(
            "/api/v1/people/export", {"scope": f"group:{self.team.id}"}
        )
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="team.vcf"'
        )
        text = response.content.decode()
        self.assertIn("FN:Team Contact", text)
        self.assertNotIn("FN:Jane", text)
        self.assertNotIn("KIND:group", text)

    def test_unknown_scope_is_400(self):
        response = self.client.get("/api/v1/people/export", {"scope": "group:999"})
        self.assertEqual(response.status_code, 400)

    def test_export_one_person(self):
        response = self.client.get(
            "/api/v1/people/export", {"person": str(self.jane.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="Jane \\"JD\\" Doe.vcf"',
        )
        text = response.content.decode()
        self.assertEqual(text.count("BEGIN:VCARD"), 1)
        self.assertIn("EMAIL;TYPE=work:jane@example.com", text)

    def test_export_foreign_person_is_404(self):
        response = self.client.get(
            "/api/v1/people/export", {"person": str(self.bobs.uuid)}
        )
        self.assertEqual(response.status_code, 404)

    def test_malformed_filters_are_400(self):
        for name in ("person", "list"):
            with self.subTest(name):
                response = self.client.get("/api/v1/people/export", {name: "nope"})
                self.assertEqual(response.status_code, 400)
                self.assertIn(name, response.data)

    def test_person_wins_over_list_and_scope(self):
        response = self.client.get(
            "/api/v1/people/export",
            {
                "person": str(self.jane.uuid),
                "list": str(self.friends.uuid),
                "scope": f"group:{self.team.id}",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("BEGIN:VCARD"), 1)

    def test_export_list_with_members(self):
        response = self.client.get(
            "/api/v1/people/export", {"list": str(self.friends.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="Friends.vcf"'
        )
        text = response.content.decode()
        self.assertEqual(text.count("BEGIN:VCARD"), 2)
        self.assertIn("FN:Jane", text)
        self.assertIn("KIND:group", text)

    def test_export_foreign_list_is_404(self):
        theirs = create_list(owner=self.bob, name="Theirs")
        response = self.client.get("/api/v1/people/export", {"list": str(theirs.uuid)})
        self.assertEqual(response.status_code, 404)

    def test_old_vcf_routes_are_gone(self):
        self.assertEqual(
            self.client.get(f"/api/v1/people/{self.jane.uuid}/vcf").status_code, 404
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/people/lists/{self.friends.uuid}/vcf"
            ).status_code,
            404,
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
            "/api/v1/people/actions", {"uuids": [str(self.jane.uuid)]}, format="json"
        )
        ids = [a["id"] for a in response.data[str(self.jane.uuid)]]
        self.assertIn("export", ids)
        self.assertLess(ids.index("export"), ids.index("delete"))
        self.assertEqual(PersonList.objects.count(), 1)
