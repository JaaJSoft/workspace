import uuid

from django.db import connection
from django.test import TestCase, TransactionTestCase

from workspace.common.search import fts5_available
from workspace.mail.search import search_mail


class FtsSchemaTests(TestCase):
    def test_sqlite_fts_table_exists(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only schema check")
        with connection.cursor() as c:
            c.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='mail_message_fts'"
            )
            self.assertIsNotNone(c.fetchone())

    def test_sqlite_insert_triggers_index(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only trigger check")
        from django.contrib.auth import get_user_model

        from workspace.mail.models import MailAccount, MailFolder, MailMessage

        user = get_user_model().objects.create_user(username="u", password="x")
        account = MailAccount.objects.create(
            owner=user,
            email="u@x.io",
            imap_host="imap.x.io",
            smtp_host="smtp.x.io",
            username="u@x.io",
        )
        folder = MailFolder.objects.create(
            account=account,
            name="INBOX",
            display_name="Inbox",
        )
        msg = MailMessage.objects.create(
            account=account,
            folder=folder,
            imap_uid=1,
            subject="Quarterly synergy report",
            snippet="body",
        )
        with connection.cursor() as c:
            c.execute(
                "SELECT rowid FROM mail_message_fts WHERE mail_message_fts MATCH %s",
                ('"synergy"',),
            )
            hit = c.fetchone()
        self.assertIsNotNone(hit)
        # MailMessage's primary key is `uuid`; the FTS table is keyed on the
        # implicit SQLite rowid, so resolve it back with raw SQL rather than
        # the ORM (which has no `rowid` field to filter on). SQLite stores
        # the UUID as a hyphen-less hex string, hence uuid.UUID() below.
        with connection.cursor() as c:
            c.execute("SELECT uuid FROM mail_mailmessage WHERE rowid = %s", [hit[0]])
            matched_uuid = c.fetchone()[0]
        self.assertEqual(uuid.UUID(hex=matched_uuid), msg.uuid)


class SearchMailBehaviorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        from workspace.mail.models import MailAccount, MailFolder, MailMessage

        cls.user = get_user_model().objects.create_user(username="s", email="s@x.io")
        cls.account = MailAccount.objects.create(
            owner=cls.user,
            email="s@x.io",
            imap_host="imap.x.io",
            smtp_host="smtp.x.io",
            username="s@x.io",
        )
        cls.folder = MailFolder.objects.create(
            account=cls.account,
            name="INBOX",
            display_name="Inbox",
        )
        cls.m1 = MailMessage.objects.create(
            account=cls.account,
            folder=cls.folder,
            imap_uid=1,
            subject="Résumé for the role",
            snippet="cv attached",
        )
        cls.m2 = MailMessage.objects.create(
            account=cls.account,
            folder=cls.folder,
            imap_uid=2,
            subject="Lunch plans",
            snippet="pizza",
        )

    def test_finds_by_subject(self):
        hits = search_mail("lunch", self.user, limit=10)
        self.assertEqual([h.uuid for h in hits], [str(self.m2.uuid)])

    def test_accent_insensitive(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required for the accent path")
        hits = search_mail("resume", self.user, limit=10)  # query without accent
        self.assertIn(str(self.m1.uuid), [h.uuid for h in hits])

    def test_malformed_query_does_not_crash(self):
        # A lone quote/dash would raise fts5 syntax error if unsanitized.
        hits = search_mail('résumé" -role', self.user, limit=10)
        self.assertIsInstance(hits, list)

    def test_empty_query_returns_no_crash(self):
        self.assertEqual(search_mail("   ", self.user, limit=10), [])


class TriggerRebuildTests(TransactionTestCase):
    """The post_migrate handler must restore FTS sync after triggers are lost.

    Uses TransactionTestCase (not TestCase): `executescript` on SQLite issues
    an implicit COMMIT, which would break plain TestCase's rollback-based
    isolation and leak rows into later tests. TransactionTestCase cleans up
    by flushing tables instead of relying on a rollback.
    """

    def test_rebuild_after_triggers_dropped(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only resilience path")
        from django.contrib.auth import get_user_model

        from workspace.mail.apps import rebuild_sqlite_fts
        from workspace.mail.models import MailAccount, MailFolder, MailMessage

        user = get_user_model().objects.create_user(username="r", email="r@x.io")
        account = MailAccount.objects.create(
            owner=user,
            email="r@x.io",
            imap_host="imap.x.io",
            smtp_host="smtp.x.io",
            username="r@x.io",
        )
        folder = MailFolder.objects.create(
            account=account,
            name="INBOX",
            display_name="Inbox",
        )
        with connection.cursor() as c:
            c.execute("DROP TRIGGER IF EXISTS mail_message_fts_ai")
            c.execute("DROP TRIGGER IF EXISTS mail_message_fts_ad")
            c.execute("DROP TRIGGER IF EXISTS mail_message_fts_au")

        rebuild_sqlite_fts(sender=None, using=connection.alias)

        msg = MailMessage.objects.create(
            account=account,
            folder=folder,
            imap_uid=1,
            subject="Reindexed subject",
            snippet="x",
        )
        hits = search_mail("reindexed", user, limit=10)
        self.assertIn(str(msg.uuid), [h.uuid for h in hits])
