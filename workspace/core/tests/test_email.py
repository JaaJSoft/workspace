"""The instance's own mail: rendering, headers, limits, suppressions, delivery."""

import smtplib
from datetime import timedelta
from unittest.mock import patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.core.models import EmailDelivery, EmailSuppression
from workspace.core.services import email as email_service
from workspace.core.services.email import (
    MAX_DELIVERY_ATTEMPTS,
    deliver,
    email_configured,
    email_unavailable_reason,
    purge_deliveries,
    record_bounce,
    send_email,
    suppress_address,
    unsubscribe_target,
)
from workspace.core.tasks import deliver_email

User = get_user_model()

TEMPLATE = "core/email/admin_test"

ENABLED = override_settings(
    EMAIL_ENABLED=True,
    EMAIL_BASE_URL="https://ws.example.com",
    DEFAULT_FROM_EMAIL="Acme Workspace <noreply@mail.example.com>",
    EMAIL_REPLY_TO=["support@example.com"],
    EMAIL_RATE_LIMIT_PER_RECIPIENT=20,
    EMAIL_RATE_LIMIT_GLOBAL=500,
)


class _EmailTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw"
        )

    def send(self, to="alice@example.com", **kwargs):
        """send_email with its on_commit delivery run, as after a real commit."""
        kwargs.setdefault("feature", "test")
        kwargs.setdefault("context", {"requested_by": self.user})
        with self.captureOnCommitCallbacks(execute=True):
            delivery = send_email(to, kwargs.pop("template", TEMPLATE), **kwargs)
        if delivery is not None:
            delivery.refresh_from_db()
        return delivery


class DisabledTests(_EmailTestCase):
    """The default: no configuration, so the instance behaves as it did before
    the service existed."""

    def test_nothing_is_sent_recorded_or_raised(self):
        with self.assertLogs("workspace.core.services.email", "INFO") as logs:
            delivery = self.send()
        self.assertIsNone(delivery)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(EmailDelivery.objects.exists())
        self.assertIn("not sent", logs.output[0])

    def test_reports_why(self):
        self.assertFalse(email_configured())
        self.assertIn("not configured", email_unavailable_reason())

    def test_the_logged_address_is_scrubbed(self):
        with self.assertLogs("workspace.core.services.email", "INFO") as logs:
            self.send(to="alice@example.com\r\nforged line")
        self.assertNotIn("\n", logs.records[0].getMessage())


@ENABLED
class SendTests(_EmailTestCase):
    def test_sends_text_and_html(self):
        delivery = self.send()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["alice@example.com"])
        self.assertEqual(
            message.from_email, "Acme Workspace <noreply@mail.example.com>"
        )
        self.assertEqual(message.reply_to, ["support@example.com"])
        self.assertEqual(message.subject, "Test email from Acme Workspace")
        self.assertIn("Hello alice", message.body)
        [(html, mimetype)] = message.alternatives
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Hello alice", html)
        self.assertNotIn("<img", html)

        self.assertEqual(delivery.status, EmailDelivery.Status.SENT)
        self.assertEqual(delivery.attempts, 1)
        self.assertIsNotNone(delivery.sent_at)
        self.assertEqual(delivery.feature, "test")
        self.assertEqual(delivery.template, TEMPLATE)
        self.assertEqual(delivery.subject, "Test email from Acme Workspace")

    def test_headers(self):
        delivery = self.send()
        headers = mail.outbox[0].message()

        self.assertEqual(headers["Message-ID"], delivery.message_id)
        self.assertTrue(delivery.message_id.endswith("@mail.example.com>"))
        self.assertIsNotNone(headers["Date"])
        self.assertEqual(headers["Auto-Submitted"], "auto-generated")
        self.assertIsNone(headers["References"])
        # A password reset must not offer to unsubscribe from password resets.
        self.assertIsNone(headers["List-Unsubscribe"])
        self.assertIsNone(headers["List-Unsubscribe-Post"])

    def test_non_transactional_mail_carries_one_click_unsubscribe(self):
        self.send(transactional=False, feature="digest")
        message = mail.outbox[0]
        headers = message.message()

        self.assertEqual(headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        url = headers["List-Unsubscribe"].strip("<>")
        self.assertTrue(url.startswith("https://ws.example.com/email/unsubscribe/"))
        token = url.rsplit("/", 1)[1]
        self.assertEqual(unsubscribe_target(token), ("alice@example.com", "digest"))
        # The link is in both parts: the text part is the whole mail for a
        # client that shows no HTML.
        self.assertIn(url, message.body)
        self.assertIn(url, message.alternatives[0][0])

    @override_settings(EMAIL_BASE_URL="")
    def test_non_transactional_mail_needs_a_base_url(self):
        self.assertIsNotNone(email_unavailable_reason(transactional=False))
        self.assertIsNone(self.send(transactional=False))
        self.assertEqual(mail.outbox, [])
        # Transactional mail needs no link and still goes out.
        self.assertTrue(email_configured())

    def test_mails_about_one_object_share_a_thread(self):
        self.send(thread_key="event:42")
        self.send(thread_key="event:42")
        self.send(thread_key="event:43")
        first, second, other = (m.message() for m in mail.outbox)

        self.assertEqual(first["References"], second["References"])
        self.assertEqual(first["In-Reply-To"], first["References"])
        self.assertNotEqual(first["References"], other["References"])
        self.assertTrue(first["References"].endswith("@mail.example.com>"))
        self.assertNotIn("event", first["References"])
        self.assertNotEqual(first["Message-ID"], second["Message-ID"])

    def test_text_part_is_not_html_escaped(self):
        user = User.objects.create_user(username="o'brien&co", email="ob@example.com")
        self.send(to=user.email, context={"requested_by": user})
        message = mail.outbox[0]

        self.assertIn("Hello o'brien&co", message.body)
        self.assertIn("o&#x27;brien&amp;co", message.alternatives[0][0])

    def test_address_is_normalized(self):
        delivery = self.send(to="  Alice@Example.COM ")
        self.assertEqual(delivery.to_address, "alice@example.com")
        self.assertEqual(mail.outbox[0].to, ["alice@example.com"])

    def test_refuses_something_that_is_not_an_address(self):
        with self.assertRaises(ValueError):
            send_email("not an address", TEMPLATE, feature="test")
        self.assertFalse(EmailDelivery.objects.exists())

    def test_records_the_user(self):
        delivery = self.send(user=self.user)
        self.assertEqual(delivery.user, self.user)

    def test_delivery_waits_for_the_commit(self):
        with self.captureOnCommitCallbacks() as callbacks:
            delivery = send_email(
                "alice@example.com",
                TEMPLATE,
                feature="test",
                context={"requested_by": self.user},
            )
        self.assertEqual(mail.outbox, [])
        self.assertEqual(delivery.status, EmailDelivery.Status.QUEUED)
        self.assertEqual(len(callbacks), 1)


@ENABLED
class RateLimitTests(_EmailTestCase):
    @override_settings(EMAIL_RATE_LIMIT_PER_RECIPIENT=2)
    def test_per_recipient(self):
        self.send()
        self.send()
        limited = self.send()
        other = self.send(to="bob@example.com")

        self.assertEqual(limited.status, EmailDelivery.Status.RATE_LIMITED)
        self.assertIn("Per-recipient", limited.error)
        self.assertEqual(other.status, EmailDelivery.Status.SENT)
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(EMAIL_RATE_LIMIT_GLOBAL=2)
    def test_per_instance(self):
        self.send(to="a@example.com")
        self.send(to="b@example.com")
        limited = self.send(to="c@example.com")

        self.assertEqual(limited.status, EmailDelivery.Status.RATE_LIMITED)
        self.assertIn("Instance", limited.error)
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(EMAIL_RATE_LIMIT_PER_RECIPIENT=1)
    def test_the_window_slides(self):
        self.send()
        EmailDelivery.objects.update(created_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(self.send().status, EmailDelivery.Status.SENT)

    @override_settings(EMAIL_RATE_LIMIT_PER_RECIPIENT=1)
    def test_refused_mails_do_not_use_up_the_limit(self):
        suppress_address("alice@example.com", EmailSuppression.Reason.MANUAL)
        self.send()
        EmailSuppression.objects.all().delete()
        self.assertEqual(self.send().status, EmailDelivery.Status.SENT)


@ENABLED
class SuppressionTests(_EmailTestCase):
    def test_a_hard_bounce_stops_every_mail(self):
        suppress_address("ALICE@example.com", EmailSuppression.Reason.HARD_BOUNCE)
        delivery = self.send()

        self.assertEqual(delivery.status, EmailDelivery.Status.SUPPRESSED)
        self.assertIn("Hard bounce", delivery.error)
        self.assertEqual(mail.outbox, [])

    def test_an_unsubscribe_stops_its_feature_only(self):
        suppress_address(
            "alice@example.com", EmailSuppression.Reason.UNSUBSCRIBED, feature="digest"
        )
        blocked = self.send(feature="digest", transactional=False)
        allowed = self.send(feature="password_reset")

        self.assertEqual(blocked.status, EmailDelivery.Status.SUPPRESSED)
        self.assertEqual(allowed.status, EmailDelivery.Status.SENT)

    def test_a_suppression_landing_before_delivery_wins(self):
        with self.captureOnCommitCallbacks() as callbacks:
            delivery = send_email(
                "alice@example.com",
                TEMPLATE,
                feature="test",
                context={"requested_by": self.user},
            )
        record_bounce("alice@example.com", EmailSuppression.Reason.COMPLAINT)
        for callback in callbacks:
            callback()

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.SUPPRESSED)
        self.assertEqual(mail.outbox, [])

    def test_suppressing_twice_keeps_the_first_reason(self):
        suppress_address("a@example.com", EmailSuppression.Reason.HARD_BOUNCE)
        again = suppress_address("a@example.com", EmailSuppression.Reason.COMPLAINT)
        self.assertEqual(again.reason, EmailSuppression.Reason.HARD_BOUNCE)
        self.assertEqual(EmailSuppression.objects.count(), 1)

    def test_a_bounce_marks_the_mail_it_names(self):
        delivery = self.send()
        record_bounce(
            "alice@example.com",
            EmailSuppression.Reason.HARD_BOUNCE,
            message_id=delivery.message_id.strip("<>"),
            detail="550 5.1.1 user unknown",
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.BOUNCED)
        self.assertIn("user unknown", delivery.error)


@ENABLED
class DeliveryFailureTests(_EmailTestCase):
    def queue(self):
        with self.captureOnCommitCallbacks():
            delivery = send_email(
                "alice@example.com",
                TEMPLATE,
                feature="test",
                context={"requested_by": self.user},
            )
        return delivery

    def attempt(self, delivery, error, *, final_attempt=False):
        with patch.object(EmailMultiAlternatives, "send", side_effect=error):
            with self.assertLogs("workspace.core.services.email", "WARNING"):
                retry = deliver(
                    str(delivery.uuid),
                    subject="s",
                    text="t",
                    html="<p>h</p>",
                    headers={},
                    final_attempt=final_attempt,
                )
        delivery.refresh_from_db()
        return retry

    def test_a_transient_failure_asks_for_a_retry(self):
        delivery = self.queue()
        retry = self.attempt(delivery, smtplib.SMTPServerDisconnected("gone"))

        self.assertTrue(retry)
        self.assertEqual(delivery.status, EmailDelivery.Status.QUEUED)
        self.assertEqual(delivery.attempts, 1)
        self.assertIn("gone", delivery.error)

    def test_a_timeout_is_transient(self):
        delivery = self.queue()
        self.assertTrue(self.attempt(delivery, TimeoutError("timed out")))

    def test_a_4xx_reply_is_transient(self):
        delivery = self.queue()
        error = smtplib.SMTPDataError(451, b"try again later")
        self.assertTrue(self.attempt(delivery, error))

    def test_the_last_attempt_gives_up(self):
        delivery = self.queue()
        retry = self.attempt(
            delivery, ConnectionRefusedError("refused"), final_attempt=True
        )
        self.assertFalse(retry)
        self.assertEqual(delivery.status, EmailDelivery.Status.FAILED)

    def test_a_5xx_reply_is_final(self):
        delivery = self.queue()
        error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
        retry = self.attempt(delivery, error)

        self.assertFalse(retry)
        self.assertEqual(delivery.status, EmailDelivery.Status.FAILED)
        self.assertIn("535 bad credentials", delivery.error)
        # Our credentials, not the recipient's mailbox.
        self.assertFalse(EmailSuppression.objects.exists())

    def test_a_refused_recipient_is_a_hard_bounce(self):
        delivery = self.queue()
        error = smtplib.SMTPRecipientsRefused(
            {"alice@example.com": (550, b"5.1.1 no such user")}
        )
        retry = self.attempt(delivery, error)

        self.assertFalse(retry)
        self.assertEqual(delivery.status, EmailDelivery.Status.FAILED)
        suppression = EmailSuppression.objects.get()
        self.assertEqual(suppression.address, "alice@example.com")
        self.assertEqual(suppression.reason, EmailSuppression.Reason.HARD_BOUNCE)
        self.assertEqual(suppression.feature, "")

    def test_a_settled_mail_is_not_sent_again(self):
        delivery = self.queue()
        EmailDelivery.objects.update(status=EmailDelivery.Status.SENT)
        retry = deliver(
            str(delivery.uuid),
            subject="s",
            text="t",
            html="h",
            headers={},
            final_attempt=False,
        )
        self.assertFalse(retry)
        self.assertEqual(mail.outbox, [])


class DeliveryTaskTests(TestCase):
    def run_attempt(self, retries, *, retry):
        """Run the task as a worker would on its ``retries``-th retry; returns
        what it passed as ``final_attempt`` and the countdown it retried with."""
        deliver_email.push_request(retries=retries, is_eager=False)
        try:
            with (
                patch.object(email_service, "deliver", return_value=retry) as deliver_,
                patch.object(
                    deliver_email, "retry", side_effect=lambda countdown: Retry()
                ) as retry_,
            ):
                try:
                    deliver_email.run(
                        "00000000-0000-0000-0000-000000000000",
                        subject="s",
                        text="t",
                        html="h",
                        headers={},
                    )
                except Retry:
                    pass
        finally:
            deliver_email.pop_request()
        countdown = retry_.call_args.kwargs["countdown"] if retry_.called else None
        return deliver_.call_args.kwargs["final_attempt"], countdown

    def test_retries_with_exponential_backoff(self):
        countdowns = [
            self.run_attempt(retries, retry=True)[1]
            for retries in range(MAX_DELIVERY_ATTEMPTS - 1)
        ]
        self.assertEqual(countdowns, [60, 120, 240, 480, 960])

    def test_only_the_last_attempt_is_final(self):
        finals = [
            self.run_attempt(retries, retry=False)[0]
            for retries in range(MAX_DELIVERY_ATTEMPTS)
        ]
        self.assertEqual(finals, [False] * (MAX_DELIVERY_ATTEMPTS - 1) + [True])

    @override_settings(EMAIL_ENABLED=True)
    def test_an_eager_run_makes_one_attempt(self):
        """Without a worker, a retry would run inside the request that queued
        the mail and raise out of it: the admin's test mail answered 500."""
        user = User.objects.create_user(username="eager", email="e@example.com")

        def run_eagerly(args, kwargs, priority):
            return deliver_email.apply(args=args, kwargs=kwargs, throw=True)

        with (
            patch.object(deliver_email, "apply_async", side_effect=run_eagerly),
            patch.object(
                EmailMultiAlternatives,
                "send",
                side_effect=ConnectionRefusedError("refused"),
            ) as send,
            self.assertLogs("workspace.core.services.email", "ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            delivery = send_email(
                user.email,
                TEMPLATE,
                feature="test",
                context={"requested_by": user},
            )
        send.assert_called_once()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.FAILED)


@ENABLED
class PurgeTests(_EmailTestCase):
    def test_deletes_old_records_only(self):
        old = self.send()
        recent = self.send()
        EmailDelivery.objects.filter(uuid=old.uuid).update(
            created_at=timezone.now() - timedelta(days=100)
        )

        deleted = purge_deliveries(timezone.now() - timedelta(days=90))

        self.assertEqual(deleted, 1)
        self.assertEqual(list(EmailDelivery.objects.all()), [recent])


class UnsubscribeTokenTests(TestCase):
    def test_rejects_a_forged_token(self):
        self.assertIsNone(unsubscribe_target("forged"))

    def test_rejects_a_token_signed_for_something_else(self):
        from django.core import signing

        token = signing.dumps({"a": "a@example.com", "f": "digest"})
        self.assertIsNone(unsubscribe_target(token))
