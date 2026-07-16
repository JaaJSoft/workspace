from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.message_search import search_messages_qs
from workspace.common.search import fts5_available

User = get_user_model()


def make_conversation(owner, *members, kind="group", title="Room"):
    conv = Conversation.objects.create(kind=kind, title=title, created_by=owner)
    for user in (owner, *members):
        ConversationMember.objects.create(conversation=conv, user=user)
    return conv


class FtsSchemaTests(TestCase):
    def test_sqlite_fts_table_exists(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only schema check")
        with connection.cursor() as c:
            c.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='chat_message_fts'"
            )
            self.assertIsNotNone(c.fetchone())

    def test_sqlite_triggers_track_insert_update_delete(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only trigger check")
        alice = User.objects.create_user(username="a", email="a@x.io")
        conv = make_conversation(alice)
        msg = Message.objects.create(
            conversation=conv, author=alice, body="the zanzibar report"
        )

        def match(term):
            with connection.cursor() as c:
                c.execute(
                    "SELECT rowid FROM chat_message_fts "
                    "WHERE chat_message_fts MATCH %s",
                    (f'"{term}"',),
                )
                return c.fetchone()

        self.assertIsNotNone(match("zanzibar"))

        msg.body = "the yokohama report"
        msg.save(update_fields=["body"])
        self.assertIsNone(match("zanzibar"))
        self.assertIsNotNone(match("yokohama"))

        msg.delete()
        self.assertIsNone(match("yokohama"))


class SearchServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", email="al@x.io")
        cls.bob = User.objects.create_user(username="bob", email="bo@x.io")
        cls.conv_shared = make_conversation(cls.alice, cls.bob, title="Shared")
        cls.conv_bob_only = make_conversation(cls.bob, title="Private")
        cls.m_shared = Message.objects.create(
            conversation=cls.conv_shared,
            author=cls.bob,
            body="the quarterly kumquat budget is ready",
        )
        cls.m_private = Message.objects.create(
            conversation=cls.conv_bob_only,
            author=cls.bob,
            body="secret kumquat plans nobody else may read",
        )

    def test_finds_by_body(self):
        hits = list(search_messages_qs(self.alice, "kumquat"))
        self.assertEqual([m.uuid for m in hits], [self.m_shared.uuid])

    def test_access_control_excludes_non_member_conversations(self):
        # Bob sees both, Alice only the shared one - even though the term
        # matches in both conversations.
        self.assertEqual(len(list(search_messages_qs(self.bob, "kumquat"))), 2)
        self.assertEqual(len(list(search_messages_qs(self.alice, "kumquat"))), 1)

    def test_left_member_loses_access(self):
        membership = ConversationMember.objects.get(
            conversation=self.conv_shared, user=self.alice
        )
        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])
        self.assertEqual(list(search_messages_qs(self.alice, "kumquat")), [])

    def test_conversation_scope(self):
        hits = list(
            search_messages_qs(
                self.bob, "kumquat", conversation_id=self.conv_bob_only.uuid
            )
        )
        self.assertEqual([m.uuid for m in hits], [self.m_private.uuid])

    def test_deleted_messages_excluded(self):
        Message.objects.filter(pk=self.m_shared.pk).update(deleted_at=timezone.now())
        self.assertEqual(list(search_messages_qs(self.alice, "kumquat")), [])

    def test_accent_insensitive(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required for the accent path")
        Message.objects.create(
            conversation=self.conv_shared,
            author=self.alice,
            body="la réunion est décalée",
        )
        hits = list(search_messages_qs(self.alice, "reunion"))
        self.assertEqual(len(hits), 1)

    def test_term_frequency_ranks_higher(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("ranking asserted on the FTS5 path only")
        twice = Message.objects.create(
            conversation=self.conv_shared,
            author=self.alice,
            body="pretzel pretzel discussion about snacks",
        )
        once = Message.objects.create(
            conversation=self.conv_shared,
            author=self.alice,
            body="pretzel mention in passing here too",
        )
        hits = [m.uuid for m in search_messages_qs(self.alice, "pretzel")]
        self.assertLess(hits.index(twice.uuid), hits.index(once.uuid))

    def test_equal_rank_falls_back_to_newest_first(self):
        older = Message.objects.create(
            conversation=self.conv_shared, author=self.alice, body="same walrus text"
        )
        newer = Message.objects.create(
            conversation=self.conv_shared, author=self.alice, body="same walrus text"
        )
        hits = [m.uuid for m in search_messages_qs(self.alice, "walrus")]
        self.assertEqual(hits, [newer.uuid, older.uuid])

    def test_malformed_query_does_not_crash(self):
        hits = list(search_messages_qs(self.alice, 'kumquat" -budget'))
        self.assertIsInstance(hits, list)

    def test_blank_query_returns_no_rows(self):
        self.assertEqual(list(search_messages_qs(self.alice, "   ")), [])


class TriggerRebuildTests(TransactionTestCase):
    """TransactionTestCase: executescript commits implicitly, which would
    break plain TestCase rollback isolation."""

    def test_rebuild_after_triggers_dropped(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only resilience path")
        from workspace.common.search.schema import rebuild_sqlite_fts_indexes

        alice = User.objects.create_user(username="r", email="r@x.io")
        conv = make_conversation(alice)
        with connection.cursor() as c:
            c.execute("DROP TRIGGER IF EXISTS chat_message_fts_ai")
            c.execute("DROP TRIGGER IF EXISTS chat_message_fts_ad")
            c.execute("DROP TRIGGER IF EXISTS chat_message_fts_au")

        rebuild_sqlite_fts_indexes(sender=None, using=connection.alias)

        msg = Message.objects.create(
            conversation=conv, author=alice, body="postrebuild keyword present"
        )
        hits = [m.uuid for m in search_messages_qs(alice, "postrebuild")]
        self.assertIn(msg.uuid, hits)


class GlobalMessageProviderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="galice", email="ga@x.io")
        cls.bob = User.objects.create_user(username="gbob", email="gb@x.io")
        cls.conv = make_conversation(cls.alice, cls.bob, title="Design room")
        cls.msg = Message.objects.create(
            conversation=cls.conv,
            author=cls.bob,
            body="the flamingo mockups are ready for review",
        )

    def test_returns_search_results_for_matching_messages(self):
        from workspace.chat.search import search_chat_messages

        results = search_chat_messages("flamingo", self.alice, 10)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.uuid, str(self.msg.uuid))
        self.assertEqual(r.name, "Design room")
        self.assertEqual(r.url, f"/chat/{self.conv.uuid}")
        self.assertIn("flamingo", r.matched_value)
        self.assertEqual(r.module_slug, "chat")

    def test_untitled_group_result_named_after_author(self):
        # Only DMs borrow the other member's name; an untitled group must
        # fall back to the message author, not an arbitrary member.
        from workspace.chat.search import search_chat_messages

        group = make_conversation(self.alice, self.bob, title="")
        Message.objects.create(
            conversation=group,
            author=self.alice,
            body="the heron budget needs a look",
        )
        results = search_chat_messages("heron", self.alice, 10)
        self.assertEqual(results[0].name, "galice")

    def test_excerpt_is_truncated(self):
        from workspace.chat.search import search_chat_messages

        Message.objects.create(
            conversation=self.conv,
            author=self.bob,
            body="pelican " + "x" * 500,
        )
        results = search_chat_messages("pelican", self.alice, 10)
        self.assertLessEqual(len(results[0].matched_value), 120)

    def test_dm_result_named_after_other_member_not_searcher(self):
        from workspace.chat.search import search_chat_messages

        dm = make_conversation(self.alice, self.bob, kind="dm", title="")
        Message.objects.create(
            conversation=dm,
            author=self.alice,
            body="the aardvark schedule is confirmed",
        )

        results = search_chat_messages("aardvark", self.alice, 10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "gbob")

    def test_dm_result_falls_back_to_author_when_no_other_member(self):
        from workspace.chat.search import search_chat_messages

        solo = Conversation.objects.create(kind="dm", title="", created_by=self.alice)
        ConversationMember.objects.create(conversation=solo, user=self.alice)
        Message.objects.create(
            conversation=solo,
            author=self.alice,
            body="the narwhal reminder to self",
        )

        results = search_chat_messages("narwhal", self.alice, 10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "galice")

    def test_provider_is_registered(self):
        # ModuleRegistry has no public listing accessor for search
        # providers (only register_search_provider/search); the apps.py
        # ready() registration already ran when Django loaded the app
        # registry, so re-registering the same slug raising is proof
        # "chat-messages" is registered.
        from workspace.core.module_registry import SearchProviderInfo, registry

        with self.assertRaises(ValueError):
            registry.register_search_provider(
                SearchProviderInfo(
                    slug="chat-messages",
                    module_slug="chat",
                    search_fn=lambda q, u, limit: [],
                )
            )
