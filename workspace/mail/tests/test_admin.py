from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.mail.models import (
    MailAccount,
    MailExtraction,
    MailFolder,
    MailLabel,
    MailMessage,
    MailRule,
)

User = get_user_model()


def _make_account(owner, email, **overrides):
    fields = {
        "owner": owner,
        "email": email,
        "imap_host": "imap.test",
        "smtp_host": "smtp.test",
        "username": email,
    }
    fields.update(overrides)
    return MailAccount.objects.create(**fields)


class MailAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.account_ok = _make_account(
            cls.admin, "ok@test.com", last_sync_at=timezone.now()
        )
        cls.account_error = _make_account(
            cls.admin, "bad@test.com", last_sync_error="auth failed"
        )
        cls.account_inactive = _make_account(cls.admin, "off@test.com", is_active=False)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_account_change_list_renders_sync_health(self):
        response = self.client.get(reverse("admin:mail_mailaccount_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ok@test.com")

    def test_sync_now_queues_active_accounts_only(self):
        with patch("workspace.mail.tasks.sync_single_account.delay") as delay:
            response = self.client.post(
                reverse("admin:mail_mailaccount_changelist"),
                {
                    "action": "sync_now",
                    "_selected_action": [
                        str(self.account_ok.uuid),
                        str(self.account_inactive.uuid),
                    ],
                },
            )
        self.assertEqual(response.status_code, 302)
        delay.assert_called_once_with(str(self.account_ok.uuid))

    def test_rule_actions_toggle_is_enabled(self):
        rule = MailRule.objects.create(
            account=self.account_ok, name="Newsletters", is_enabled=False
        )
        url = reverse("admin:mail_mailrule_changelist")

        self.client.post(
            url, {"action": "enable_rules", "_selected_action": [str(rule.uuid)]}
        )
        rule.refresh_from_db()
        self.assertTrue(rule.is_enabled)

        self.client.post(
            url, {"action": "disable_rules", "_selected_action": [str(rule.uuid)]}
        )
        rule.refresh_from_db()
        self.assertFalse(rule.is_enabled)

    def test_label_change_list_renders(self):
        MailLabel.objects.create(account=self.account_ok, name="Invoices")
        response = self.client.get(reverse("admin:mail_maillabel_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoices")

    def test_extraction_change_list_renders_and_is_read_only(self):
        folder = MailFolder.objects.create(
            account=self.account_ok,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        message = MailMessage.objects.create(
            account=self.account_ok,
            folder=folder,
            imap_uid=1,
            subject="Dinner on Friday",
        )
        MailExtraction.objects.create(
            mail_message=message, kind=MailExtraction.Kind.EVENT
        )
        response = self.client.get(reverse("admin:mail_mailextraction_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dinner on Friday")
        self.assertEqual(
            self.client.get(reverse("admin:mail_mailextraction_add")).status_code, 403
        )

    def test_rule_logs_cannot_be_added_by_hand(self):
        self.assertEqual(
            self.client.get(reverse("admin:mail_mailrulelog_add")).status_code, 403
        )
