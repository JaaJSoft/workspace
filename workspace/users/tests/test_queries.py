"""Tests for workspace.users.queries.search_people."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import BotProfile
from workspace.users.queries import search_people

User = get_user_model()


class SearchPeopleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", password="pass", first_name="Alice", last_name="Martin"
        )
        cls.marie = User.objects.create_user(
            username="mdupont", password="pass", first_name="Marie", last_name="Dupont"
        )
        cls.inactive = User.objects.create_user(
            username="marianne", password="pass", is_active=False
        )
        cls.bot = User.objects.create_user(
            username="mariabot", password="pass", first_name="Maria"
        )
        BotProfile.objects.create(user=cls.bot)

    def test_matches_first_name(self):
        self.assertEqual(
            [u.username for u in search_people("marie", self.alice)], ["mdupont"]
        )

    def test_matches_last_name(self):
        self.assertEqual(
            [u.username for u in search_people("dupont", self.alice)], ["mdupont"]
        )

    def test_matches_username(self):
        self.assertEqual(
            [u.username for u in search_people("mdup", self.alice)], ["mdupont"]
        )

    def test_excludes_inactive_bots_and_the_caller(self):
        # "mar" matches alice (last name Martin), the inactive account, the bot
        # and marie - only marie is a colleague the caller can act on.
        self.assertEqual(
            [u.username for u in search_people("mar", self.alice)], ["mdupont"]
        )

    def test_without_a_requesting_user_nobody_is_excluded(self):
        self.assertEqual(
            [u.username for u in search_people("mar")], ["alice", "mdupont"]
        )

    def test_limit_caps_the_queryset(self):
        self.assertEqual(len(search_people("mar", limit=1)), 1)
