"""The migration that parses the stored Reply-To header into addresses.

Before it, the header was kept as the text it came as; a reply reads
addresses, so every message received earlier has to be parsed once.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("mail", "0041_mailmessage_is_answered")]
AFTER = [("mail", "0042_mailmessage_reply_to_addresses")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


def _latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


class ReplyToMigrationTests(TransactionTestCase):
    def setUp(self):
        old_apps = _migrate(BEFORE)
        user = old_apps.get_model("auth", "User").objects.create(username="alice")
        account = old_apps.get_model("mail", "MailAccount").objects.create(
            owner=user,
            email="alice@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="alice@example.com",
        )
        folder = old_apps.get_model("mail", "MailFolder").objects.create(
            account=account, name="INBOX", folder_type="inbox"
        )
        message_model = old_apps.get_model("mail", "MailMessage")
        self.uuids = {}
        for uid, header in enumerate(
            (
                "",
                "support@example.com",
                '"Doe, Jane" <jane@example.com>, =?utf-8?q?J=C3=BCrgen?= <j@example.de>',
                "undisclosed-recipients:;",
            ),
            start=1,
        ):
            message = message_model.objects.create(
                account=account, folder=folder, imap_uid=uid, reply_to=header
            )
            self.uuids[header] = message.uuid

    def tearDown(self):
        _migrate(_latest())

    def test_each_stored_header_becomes_its_addresses(self):
        message_model = _migrate(AFTER).get_model("mail", "MailMessage")
        parsed = dict(message_model.objects.values_list("uuid", "reply_to_addresses"))
        self.assertEqual(parsed[self.uuids[""]], [])
        self.assertEqual(
            parsed[self.uuids["support@example.com"]],
            [{"name": "", "email": "support@example.com"}],
        )
        self.assertEqual(
            parsed[
                self.uuids[
                    '"Doe, Jane" <jane@example.com>, '
                    "=?utf-8?q?J=C3=BCrgen?= <j@example.de>"
                ]
            ],
            [
                {"name": "Doe, Jane", "email": "jane@example.com"},
                {"name": "Jürgen", "email": "j@example.de"},
            ],
        )
        self.assertEqual(parsed[self.uuids["undisclosed-recipients:;"]], [])
