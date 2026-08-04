from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.services.mentions import build_mention_map

User = get_user_model()


class BuildMentionMapTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass123")
        self.dotted = User.objects.create_user(
            username="jean.dupont", password="pass123"
        )
        self.hyphenated = User.objects.create_user(
            username="marie-claire", password="pass123"
        )

    def test_plain_username(self):
        mention_map, has_everyone = build_mention_map("hi @alice")
        self.assertEqual(mention_map, {"alice": self.alice.pk})
        self.assertFalse(has_everyone)

    def test_dotted_username(self):
        mention_map, _ = build_mention_map("hi @jean.dupont")
        self.assertEqual(mention_map, {"jean.dupont": self.dotted.pk})

    def test_hyphenated_username(self):
        mention_map, _ = build_mention_map("hi @marie-claire")
        self.assertEqual(mention_map, {"marie-claire": self.hyphenated.pk})

    def test_shorter_prefix_user_is_not_notified(self):
        """@alice.dupont must not pull in 'alice' - nothing badges her."""
        User.objects.create_user(username="alice.dupont", password="pass123")
        mention_map, _ = build_mention_map("hi @alice.dupont")
        self.assertNotIn("alice", mention_map)

    def test_trailing_period_resolves_to_the_bare_username(self):
        mention_map, _ = build_mention_map("hi @alice.")
        self.assertEqual(mention_map, {"alice": self.alice.pk})

    def test_unknown_username_is_absent(self):
        mention_map, _ = build_mention_map("hi @ghost")
        self.assertEqual(mention_map, {})

    def test_everyone_is_flagged_and_mapped(self):
        mention_map, has_everyone = build_mention_map("hi @everyone")
        self.assertTrue(has_everyone)
        self.assertIn("everyone", mention_map)

    def test_email_is_not_a_mention(self):
        mention_map, _ = build_mention_map("write to alice@example.com")
        self.assertEqual(mention_map, {})

    def test_users_argument_narrows_the_pool(self):
        mention_map, _ = build_mention_map(
            "hi @alice", users=User.objects.exclude(pk=self.alice.pk)
        )
        self.assertEqual(mention_map, {})
