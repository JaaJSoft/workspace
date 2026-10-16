"""The file timeline keeps sharing history to the file's manager.

The properties panel lists shares and public links to the owner only, but the
activity timeline (HTML partial and REST endpoint) used to show any reader who
else the file was shared with, under which permission, which shares were
revoked, that public links existed - and the name of the file a copy came
from, which may sit in the copier's private tree.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from rest_framework.test import APITestCase

from workspace.files.models import FileEvent
from workspace.files.services import FileService
from workspace.files.services.sharing import (
    create_share_link,
    revoke_share_link,
    share_file,
    unshare_file,
)

User = get_user_model()

SHARING_ACTIONS = (
    FileEvent.Action.SHARED,
    FileEvent.Action.SHARE_PERMISSION_CHANGED,
    FileEvent.Action.UNSHARED,
    FileEvent.Action.LINK_CREATED,
    FileEvent.Action.LINK_REVOKED,
    FileEvent.Action.LINK_UPLOAD,
)

SHARING_TRACES = (
    "carol_secret_recipient",
    "dave_revoked",
    "HR-confidential",
    "public link",
    "permission",
    "revoked",
    "Secret-source.md",
)


class TimelineVisibilityTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.carol = User.objects.create_user(
            username="carol_secret_recipient", password="pw"
        )
        self.dave = User.objects.create_user(username="dave_revoked", password="pw")
        self.hr = Group.objects.create(name="HR-confidential")

        private = FileService.create_folder(self.alice, "MedicalPrivate")
        self.source = source = FileService.create_file(
            self.alice,
            "Secret-source.md",
            parent=private,
            content=ContentFile(b"# t\n\nhello", name="Secret-source.md"),
        )
        # Copied beside its source, so the copy is named "Secret-source (Copy).md"
        # and only the event metadata carries the source's name.
        self.doc = FileService.copy(source, private, self.alice, acting_user=self.alice)
        for user in (self.bob, self.carol, self.dave):
            share_file(
                self.doc, target_user=user, permission="ro", acting_user=self.alice
            )
        share_file(
            self.doc, target_group=self.hr, permission="ro", acting_user=self.alice
        )
        share_file(
            self.doc, target_group=self.hr, permission="rw", acting_user=self.alice
        )
        unshare_file(self.doc, target_user=self.dave, acting_user=self.alice)
        link = create_share_link(self.doc, acting_user=self.alice)
        revoke_share_link(self.doc, link_uuid=link.uuid, acting_user=self.alice)
        FileService.rename(self.doc, "renamed.md", acting_user=self.alice)

    def tearDown(self):
        cache.clear()

    def _api(self, user, **params):
        self.client.force_authenticate(user)
        response = self.client.get(f"/api/v1/files/{self.doc.uuid}/events", params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _html(self, user, **params):
        self.client.force_login(user)
        response = self.client.get(f"/files/{self.doc.uuid}/events", params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_recipient_reads_neither_shares_nor_links_nor_the_copy_source(self):
        for permission in ("ro", "rw"):
            with self.subTest(permission=permission):
                share_file(
                    self.doc,
                    target_user=self.bob,
                    permission=permission,
                    acting_user=self.alice,
                )
                html = self._html(self.bob)
                for trace in SHARING_TRACES:
                    self.assertNotIn(trace, html)
                self.assertIn("renamed", html)
                self.assertIn("copied from", html)
                self.assertIn("another file", html)

                data = self._api(self.bob)
                actions = [event["action"] for event in data["results"]]
                self.assertEqual(actions, ["renamed", "created"])
                self.assertEqual(data["count"], 2)
                created = data["results"][-1]
                self.assertNotIn("source_name", created["metadata"])
                self.assertEqual(created["metadata"], {"copied": True})
                self.assertNotIn("Secret-source.md", str(data))
                self.assertNotIn(str(self.source.uuid), str(data))
                self.assertNotIn(str(self.source.uuid), html)

    def test_the_action_filter_offers_no_sharing_action_to_a_recipient(self):
        html = self._html(self.bob, action=FileEvent.Action.SHARED)

        for action in SHARING_ACTIONS:
            self.assertNotIn(f'value="{action}"', html)
        self.assertNotIn("carol_secret_recipient", html)

    def test_a_group_member_below_manage_does_not_read_them_either(self):
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        self.bob.groups.add(team)
        root = FileService.create_folder(self.alice, "TeamRoot", group=team)
        self.doc = FileService.create_file(
            self.alice,
            "team.md",
            parent=root,
            content=ContentFile(b"x", name="team.md"),
        )
        share_file(
            self.doc, target_user=self.carol, permission="ro", acting_user=self.alice
        )

        self.assertNotIn("carol_secret_recipient", self._html(self.bob))
        self.assertEqual(
            [event["action"] for event in self._api(self.bob)["results"]], ["created"]
        )

    def test_the_owner_reads_the_whole_history(self):
        html = self._html(self.alice)
        for trace in ("carol_secret_recipient", "HR-confidential", "public link"):
            self.assertIn(trace, html)
        self.assertIn("Secret-source.md", html)

        data = self._api(self.alice, limit=50)
        actions = {event["action"] for event in data["results"]}
        self.assertLessEqual(
            {"shared", "share_permission_changed", "unshared"}, actions
        )
        self.assertLessEqual({"link_created", "link_revoked"}, actions)
        created = data["results"][-1]
        self.assertEqual(created["metadata"]["source_name"], "Secret-source.md")
        self.assertEqual(created["metadata"]["source_uuid"], str(self.source.uuid))
