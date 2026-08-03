from types import SimpleNamespace

from django.test import SimpleTestCase

from workspace.common.services.mentions import (
    extract_mentions,
    mention_badge,
    mentioned_users,
    newly_mentioned_users,
    render_comment_body,
)


class ExtractMentionsTests(SimpleTestCase):
    def test_extracts_usernames(self):
        usernames, has_everyone = extract_mentions("hi @alice and @bob")
        self.assertEqual(usernames, {"alice", "bob"})
        self.assertFalse(has_everyone)

    def test_everyone_is_flagged_separately(self):
        usernames, has_everyone = extract_mentions("@everyone plus @carol")
        self.assertEqual(usernames, {"carol"})
        self.assertTrue(has_everyone)

    def test_no_mentions(self):
        usernames, has_everyone = extract_mentions("plain text")
        self.assertEqual(usernames, set())
        self.assertFalse(has_everyone)

    def test_mid_word_at_sign_is_not_extracted(self):
        """Emails must not mention: extraction uses the same anchoring as rendering."""
        usernames, has_everyone = extract_mentions("mail foo@alice or bar@everyone")
        self.assertEqual(usernames, set())
        self.assertFalse(has_everyone)

    def test_mention_at_line_start_is_extracted(self):
        usernames, _ = extract_mentions("first line\n@alice hello")
        self.assertEqual(usernames, {"alice"})


class MentionedUsersTests(SimpleTestCase):
    def setUp(self):
        self.alice = SimpleNamespace(username="alice")
        self.bob = SimpleNamespace(username="bob")
        self.audience = [self.alice, self.bob]

    def test_selects_mentioned_audience_members_excluding_actor(self):
        result = mentioned_users(self.audience, "hi @alice and @bob", self.bob)
        self.assertEqual(result, [self.alice])

    def test_no_mentions_returns_empty(self):
        self.assertEqual(mentioned_users(self.audience, "plain text", self.bob), [])

    def test_newly_mentioned_excludes_already_mentioned(self):
        actor = SimpleNamespace(username="actor")
        result = newly_mentioned_users(
            self.audience, actor, "ping @bob", "ping @bob and @alice"
        )
        self.assertEqual(result, [self.alice])

    def test_newly_mentioned_excludes_actor(self):
        result = newly_mentioned_users(self.audience, self.bob, "draft", "draft @bob")
        self.assertEqual(result, [])


class MentionBadgeTests(SimpleTestCase):
    def test_badge_with_user_id_has_hover_card_hooks(self):
        html = mention_badge("alice", 42)
        self.assertIn('class="mention-badge"', html)
        self.assertIn('data-user-id="42"', html)
        self.assertIn("_userCardShow", html)

    def test_badge_without_user_id(self):
        html = mention_badge("alice")
        self.assertIn('data-username="alice"', html)
        self.assertNotIn("data-user-id", html)

    def test_everyone_badge(self):
        html = mention_badge("everyone")
        self.assertIn("mention-everyone", html)


class RenderCommentBodyTests(SimpleTestCase):
    def test_known_mention_becomes_badge(self):
        html = render_comment_body("ping @alice", {"alice": 42})
        self.assertIn('class="mention-badge"', html)
        self.assertIn('data-user-id="42"', html)

    def test_unknown_username_stays_literal(self):
        html = render_comment_body("ping @ghost", {"alice": 42})
        self.assertNotIn("mention-badge", html)
        self.assertIn("@ghost", html)

    def test_mid_word_at_sign_is_not_a_mention(self):
        html = render_comment_body("mail foo@alice now", {"alice": 42})
        self.assertNotIn("mention-badge", html)

    def test_html_is_escaped(self):
        html = render_comment_body('<script>alert("x")</script>', {})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_escaped_text_around_badge(self):
        html = render_comment_body("<b>hi</b> @alice", {"alice": 1})
        self.assertIn("&lt;b&gt;hi&lt;/b&gt;", html)
        self.assertIn("mention-badge", html)

    def test_mention_at_start_of_line(self):
        html = render_comment_body("first\n@alice hello", {"alice": 42})
        self.assertIn("mention-badge", html)

    def test_newlines_preserved(self):
        html = render_comment_body("line1\nline2", {})
        self.assertIn("line1\nline2", html)
