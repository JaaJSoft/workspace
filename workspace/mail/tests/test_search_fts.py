import uuid

from django.db import connection
from django.test import TestCase


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
