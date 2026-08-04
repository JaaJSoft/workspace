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


class ExtractMentionsCandidateTests(SimpleTestCase):
    """Django usernames allow [.@+-], so a token can hold several candidates."""

    def test_dotted_token_yields_the_full_username(self):
        usernames, _ = extract_mentions("hi @jean.dupont")
        self.assertIn("jean.dupont", usernames)

    def test_dotted_token_also_yields_shorter_prefixes(self):
        """The caller resolves against real users, so every prefix is offered."""
        usernames, _ = extract_mentions("hi @jean.dupont")
        self.assertIn("jean", usernames)

    def test_hyphenated_token_yields_the_full_username(self):
        usernames, _ = extract_mentions("hi @marie-claire")
        self.assertIn("marie-claire", usernames)

    def test_email_after_whitespace_is_still_not_a_mention(self):
        usernames, has_everyone = extract_mentions("write to alice@example.com now")
        self.assertEqual(usernames, set())
        self.assertFalse(has_everyone)


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


class DottedUsernameNotificationTests(SimpleTestCase):
    """A user whose username holds a dot or hyphen must still be notified."""

    def setUp(self):
        self.actor = SimpleNamespace(username="actor")
        self.dotted = SimpleNamespace(username="jean.dupont")
        self.hyphenated = SimpleNamespace(username="marie-claire")

    def test_dotted_username_is_mentioned(self):
        result = mentioned_users([self.dotted], "hi @jean.dupont", self.actor)
        self.assertEqual(result, [self.dotted])

    def test_hyphenated_username_is_mentioned(self):
        result = mentioned_users([self.hyphenated], "hi @marie-claire", self.actor)
        self.assertEqual(result, [self.hyphenated])

    def test_trailing_period_is_not_part_of_the_username(self):
        alice = SimpleNamespace(username="alice")
        self.assertEqual(mentioned_users([alice], "hi @alice.", self.actor), [alice])

    def test_only_the_longest_matching_user_is_notified(self):
        """@alice.bob mentions alice.bob, never alice - rendering agrees."""
        alice = SimpleNamespace(username="alice")
        alice_bob = SimpleNamespace(username="alice.bob")
        result = mentioned_users([alice, alice_bob], "hi @alice.bob", self.actor)
        self.assertEqual(result, [alice_bob])

    def test_newly_mentioned_handles_dotted_usernames(self):
        result = newly_mentioned_users(
            [self.dotted], self.actor, "draft", "draft @jean.dupont"
        )
        self.assertEqual(result, [self.dotted])

    def test_already_mentioned_dotted_username_is_not_renotified(self):
        result = newly_mentioned_users(
            [self.dotted], self.actor, "ping @jean.dupont", "ping @jean.dupont again"
        )
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


class RenderDottedUsernameTests(SimpleTestCase):
    """Badges must cover the whole Django username charset, not just \\w."""

    def test_dotted_username_becomes_badge(self):
        html = render_comment_body("ping @jean.dupont", {"jean.dupont": 7})
        self.assertIn('data-user-id="7"', html)
        self.assertIn(">@jean.dupont</span>", html)

    def test_hyphenated_username_becomes_badge(self):
        html = render_comment_body("ping @marie-claire", {"marie-claire": 8})
        self.assertIn('data-user-id="8"', html)
        self.assertIn(">@marie-claire</span>", html)

    def test_trailing_period_stays_outside_the_badge(self):
        html = render_comment_body("ping @alice.", {"alice": 42})
        self.assertIn(">@alice</span>.", html)

    def test_longest_known_username_wins(self):
        html = render_comment_body("ping @alice.bob", {"alice": 1, "alice.bob": 2})
        self.assertIn('data-user-id="2"', html)
        self.assertNotIn('data-user-id="1"', html)

    def test_unknown_suffix_after_a_known_username_stays_literal(self):
        html = render_comment_body("ping @alice.smith", {"alice": 1})
        self.assertIn('data-user-id="1"', html)
        self.assertIn("</span>.smith", html)

    def test_dotted_username_with_digits(self):
        html = render_comment_body("ping @lucia.bonet.4771", {"lucia.bonet.4771": 9})
        self.assertIn(">@lucia.bonet.4771</span>", html)

    def test_unknown_dotted_username_stays_literal(self):
        html = render_comment_body("ping @jean.dupont", {"alice": 1})
        self.assertNotIn("mention-badge", html)
        self.assertIn("@jean.dupont", html)
