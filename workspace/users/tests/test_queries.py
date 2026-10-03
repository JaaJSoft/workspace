"""Tests for workspace.users.queries."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import BotProfile
from workspace.users.queries import active_user_with_email, search_people

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


class SearchPeopleWithEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.carol = User.objects.create_user(
            username="carol", password="pass", email="carol@corp.com"
        )
        cls.nomail = User.objects.create_user(username="corpnomail", password="pass")

    def test_off_by_default(self):
        self.assertEqual(list(search_people("corp.com")), [])

    def test_matches_email_and_drops_users_without_one(self):
        self.assertEqual(list(search_people("corp", with_email=True)), [self.carol])


class ActiveUserWithEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.carol = User.objects.create_user(
            username="carol", password="pass", email="Carol@Corp.com"
        )
        User.objects.create_user(
            username="gone", password="pass", email="gone@corp.com", is_active=False
        )
        bot = User.objects.create_user(
            username="helper", password="pass", email="bot@corp.com"
        )
        BotProfile.objects.create(user=bot)

    def test_matches_case_insensitively(self):
        self.assertEqual(active_user_with_email(" carol@corp.COM "), self.carol)

    def test_inactive_users_and_bots_are_left_out(self):
        self.assertIsNone(active_user_with_email("gone@corp.com"))
        self.assertIsNone(active_user_with_email("bot@corp.com"))

    def test_blank_matches_nobody(self):
        User.objects.create_user(username="blank", password="pass")
        self.assertIsNone(active_user_with_email(""))
