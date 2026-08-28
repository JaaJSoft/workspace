"""Tests for the rule that every group conversation carries a name.

The sidebar labels a group from its stored title and never reads its members,
which only holds if a group cannot exist without one: created through the API,
created from an auth.Group, or predating the rule and named by the 0030
backfill.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.conversations import backfill_group_titles

User = get_user_model()


class GroupCreationTitleTests(APITestCase):
    URL = "/api/v1/chat/conversations"

    def setUp(self):
        self.creator = User.objects.create_user(
            username="creator", password="p", first_name="Cleo", last_name="Rand"
        )
        self.sam = User.objects.create_user(
            username="sam", password="p", first_name="Sam", last_name="Rivera"
        )
        self.jordan = User.objects.create_user(username="jordan", password="p")
        self.client.force_authenticate(self.creator)

    def test_a_group_created_without_a_title_is_named_after_its_members(self):
        resp = self.client.post(
            self.URL,
            {"member_ids": [self.sam.id, self.jordan.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        conversation = Conversation.objects.get(uuid=resp.json()["uuid"])
        self.assertEqual(conversation.title, "Cleo Rand, Sam Rivera, jordan")

    def test_a_supplied_title_is_kept(self):
        resp = self.client.post(
            self.URL,
            {"member_ids": [self.sam.id, self.jordan.id], "title": "Product Launch"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        conversation = Conversation.objects.get(uuid=resp.json()["uuid"])
        self.assertEqual(conversation.title, "Product Launch")

    def test_the_creator_is_named_first(self):
        """A stored title is the same for everyone, so it lists the reader too.

        The computed name it replaces excluded whoever was looking, which a
        single stored string cannot do.
        """
        resp = self.client.post(
            self.URL,
            {"member_ids": [self.sam.id, self.jordan.id]},
            format="json",
        )
        conversation = Conversation.objects.get(uuid=resp.json()["uuid"])
        self.assertTrue(conversation.title.startswith("Cleo Rand"))


class CreationResponseOrderTests(APITestCase):
    """The create response must present members the way every later read will.

    It serializes a cache built by hand rather than a queryset, so nothing
    forces it into MEMBER_ORDER - except that `bulk_create` stamps `joined_at`
    and the v7 `uuid` in list order, which is that same order. This pins that
    coincidence, since the payload feeds the member panel and the mention
    autocomplete.
    """

    URL = "/api/v1/chat/conversations"

    def setUp(self):
        # The creator is registered last, so its id is the highest: creation
        # order and user-id order disagree, which is what lets the assertion
        # below tell them apart.
        self.others = [
            User.objects.create_user(username=f"u{i}", password="p") for i in range(5)
        ]
        self.creator = User.objects.create_user(username="creator", password="p")
        self.client.force_authenticate(self.creator)

    def test_the_fresh_payload_matches_a_later_read(self):
        created = self.client.post(
            self.URL,
            {"member_ids": [u.id for u in self.others], "title": "Product Launch"},
            format="json",
        ).json()

        listed = next(
            c for c in self.client.get(self.URL).json() if c["uuid"] == created["uuid"]
        )

        self.assertEqual(
            [m["user"]["id"] for m in created["members"]],
            [m["user"]["id"] for m in listed["members"]],
        )
        self.assertEqual(created["avatar_initial"], listed["avatar_initial"])
        # The creator joined first despite carrying the highest id, so an
        # order that merely happened to be sorted by id would not look like this.
        self.assertEqual(created["members"][0]["user"]["id"], self.creator.id)


class BackfillGroupTitlesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="p", first_name="Alice", last_name="Ng"
        )
        self.bob = User.objects.create_user(username="bob", password="p")
        self.carol = User.objects.create_user(username="carol", password="p")
        self.dan = User.objects.create_user(username="dan", password="p")

    def _group(self, members, title=""):
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title=title, created_by=self.alice
        )
        for user in members:
            ConversationMember.objects.create(conversation=conversation, user=user)
        return conversation

    def _backfill(self):
        return backfill_group_titles(Conversation, ConversationMember)

    def test_an_untitled_group_is_named_after_its_first_members(self):
        group = self._group([self.alice, self.bob, self.carol, self.dan])

        self._backfill()

        group.refresh_from_db()
        self.assertEqual(group.title, "Alice Ng, bob, carol")

    def test_a_titled_group_is_left_alone(self):
        group = self._group([self.alice, self.bob], title="Product Launch")

        self._backfill()

        group.refresh_from_db()
        self.assertEqual(group.title, "Product Launch")

    def test_a_group_nobody_is_left_in_falls_back(self):
        group = self._group([])

        self._backfill()

        group.refresh_from_db()
        self.assertEqual(group.title, "Group")

    def test_a_member_who_left_is_not_named(self):
        group = self._group([self.alice, self.bob])
        ConversationMember.objects.filter(conversation=group, user=self.alice).update(
            left_at="2026-01-01T00:00:00Z"
        )

        self._backfill()

        group.refresh_from_db()
        self.assertEqual(group.title, "bob")

    def test_direct_messages_are_untouched(self):
        dm = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.alice
        )
        ConversationMember.objects.create(conversation=dm, user=self.alice)
        ConversationMember.objects.create(conversation=dm, user=self.bob)

        self._backfill()

        dm.refresh_from_db()
        self.assertEqual(dm.title, "")

    def test_running_it_twice_changes_nothing(self):
        group = self._group([self.alice, self.bob])

        self.assertEqual(self._backfill(), 1)
        self.assertEqual(self._backfill(), 0)

        group.refresh_from_db()
        self.assertEqual(group.title, "Alice Ng, bob")
