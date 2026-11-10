import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.files.models import FileFavorite, FileShare, PinnedFolder
from workspace.files.services import trash, tree
from workspace.files.services.marks import set_favorite, set_pinned

User = get_user_model()


class MarksFixture(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.docs = tree.create_folder(self.alice, "Docs")
        self.photos = tree.create_folder(self.alice, "Photos")
        self.report = tree.create_file(self.alice, "report.txt")

    def favorites(self, user):
        return set(
            FileFavorite.objects.filter(owner=user).values_list("file_id", flat=True)
        )

    def pins(self, user):
        return list(
            PinnedFolder.objects.filter(owner=user)
            .order_by("position")
            .values_list("folder_id", flat=True)
        )


class SetFavoriteTests(MarksFixture):
    def test_marks_and_unmarks_a_batch(self):
        nodes = [self.docs.pk, self.report.pk]

        self.assertEqual(sorted(set_favorite(self.alice, nodes, True)), sorted(nodes))
        self.assertEqual(self.favorites(self.alice), set(nodes))
        self.assertEqual(
            set_favorite(self.alice, nodes, True), set_favorite(self.alice, nodes, True)
        )

        set_favorite(self.alice, [self.docs.pk], False)
        self.assertEqual(self.favorites(self.alice), {self.report.pk})

    def test_a_node_out_of_reach_is_left_out_like_an_unknown_one(self):
        theirs = tree.create_file(self.bob, "secret.txt")

        done = set_favorite(self.alice, [theirs.pk, uuid.uuid4(), self.report.pk], True)

        self.assertEqual(done, [self.report.pk])
        self.assertEqual(self.favorites(self.alice), {self.report.pk})

    def test_a_shared_node_can_be_a_favorite(self):
        theirs = tree.create_file(self.bob, "shared.txt")
        FileShare.objects.create(
            file=theirs,
            shared_by=self.bob,
            shared_with=self.alice,
            permission=FileShare.Permission.READ_ONLY,
        )

        self.assertEqual(set_favorite(self.alice, [theirs.pk], True), [theirs.pk])

    def test_a_trashed_node_is_left_out(self):
        trash.soft_delete(self.report, acting_user=self.alice)

        self.assertEqual(set_favorite(self.alice, [self.report.pk], True), [])


class SetPinnedTests(MarksFixture):
    def test_new_pins_go_after_the_existing_ones_in_the_order_asked(self):
        archive = tree.create_folder(self.alice, "Archive")
        set_pinned(self.alice, [archive.pk], True)

        set_pinned(self.alice, [self.photos.pk, self.docs.pk], True)

        self.assertEqual(
            self.pins(self.alice), [archive.pk, self.photos.pk, self.docs.pk]
        )

    def test_only_folders_are_pinned(self):
        done = set_pinned(self.alice, [self.report.pk, self.docs.pk], True)

        self.assertEqual(done, [self.docs.pk])

    def test_a_groups_root_folder_is_not_pinned(self):
        team = Group.objects.create(name="Design")
        self.alice.groups.add(team)
        root = tree.create_folder(self.alice, "Design", group=team)

        self.assertEqual(set_pinned(self.alice, [root.pk], True), [])

    def test_unpinning_keeps_the_rest(self):
        set_pinned(self.alice, [self.docs.pk, self.photos.pk], True)

        self.assertEqual(set_pinned(self.alice, [self.docs.pk], False), [self.docs.pk])
        self.assertEqual(self.pins(self.alice), [self.photos.pk])


class MarksApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.docs = tree.create_folder(self.alice, "Docs")
        self.client.force_authenticate(self.alice)

    def test_favorites(self):
        response = self.client.post(
            "/api/v1/files/favorites",
            {"items": [str(self.docs.pk)], "value": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"items": [str(self.docs.pk)]})
        self.assertTrue(
            FileFavorite.objects.filter(owner=self.alice, file=self.docs).exists()
        )

    def test_pins(self):
        response = self.client.post(
            "/api/v1/files/pinned",
            {"items": [str(self.docs.pk)], "value": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"items": [str(self.docs.pk)]})
        listed = self.client.get("/api/v1/files/pinned").json()
        self.assertEqual([folder["uuid"] for folder in listed], [str(self.docs.pk)])

    def test_someone_elses_folder_answers_like_an_unknown_one(self):
        theirs = tree.create_folder(self.bob, "Theirs")

        hidden = self.client.post(
            "/api/v1/files/favorites",
            {"items": [str(theirs.pk)], "value": True},
            format="json",
        )
        absent = self.client.post(
            "/api/v1/files/favorites",
            {"items": [str(uuid.uuid4())], "value": True},
            format="json",
        )

        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_a_request_without_items_or_value_is_refused(self):
        for body in ({"items": [], "value": True}, {"items": [str(self.docs.pk)]}):
            with self.subTest(body):
                response = self.client.post(
                    "/api/v1/files/favorites", body, format="json"
                )
                self.assertEqual(response.status_code, 400)

    def test_the_item_routes_are_gone(self):
        for suffix in ("favorite", "pin"):
            with self.subTest(suffix):
                response = self.client.post(f"/api/v1/files/{self.docs.pk}/{suffix}")
                self.assertEqual(response.status_code, 404)
