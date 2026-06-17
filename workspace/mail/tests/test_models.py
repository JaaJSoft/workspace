from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount

User = get_user_model()


class MailAccountSignatureFieldTests(TestCase):
    def test_signature_defaults_to_empty_and_persists(self):
        user = User.objects.create_user(username="sig-user", password="x")
        account = MailAccount.objects.create(
            owner=user,
            email="sig@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="sig@example.com",
        )
        # Default is empty string, not null.
        self.assertEqual(account.signature, "")

        account.signature = "Cordialement\nJean"
        account.save()
        account.refresh_from_db()
        self.assertEqual(account.signature, "Cordialement\nJean")
