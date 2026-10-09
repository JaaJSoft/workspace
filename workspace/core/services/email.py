"""Mail the instance sends on its own behalf: password resets, verifications,
security alerts, notification fallbacks.

Never the user's own mail - that goes out through their account in the mail
module, and nothing here touches a ``MailAccount``. This is the single path
for everything else: no other code opens an SMTP connection
(core.tests.test_email_boundary fails on one).

``send_email`` renders the mail, records it, and hands delivery to a Celery
task; it never talks to the relay. With sending disabled it is a logged no-op
that returns ``None``, so callers need no guard of their own - though a
control that would send should ask ``email_unavailable_reason`` first and
show the reason instead of offering a button that does nothing.
"""

import hashlib
import logging
import smtplib
from contextlib import contextmanager
from datetime import timedelta
from email.utils import make_msgid, parseaddr

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.template import Context
from django.template.loader import get_template, render_to_string
from django.urls import reverse
from django.utils import timezone, translation

from workspace.common.logging import scrub
from workspace.common.task_priority import NORMAL_PRIORITY

from ..models import EmailDelivery, EmailSuppression

logger = logging.getLogger(__name__)

_UNSUBSCRIBE_SALT = "core.email.unsubscribe"

# Statuses that took a slot: a mail that was refused before it was queued
# (suppressed, rate limited) does not count against the limits it hit.
_COUNTED_STATUSES = (
    EmailDelivery.Status.QUEUED,
    EmailDelivery.Status.SENT,
    EmailDelivery.Status.FAILED,
    EmailDelivery.Status.BOUNCED,
)

_RATE_WINDOW = timedelta(hours=1)

# Delivery retries: 1, 2, 4, 8 then 16 minutes, about half an hour in all -
# long enough to ride out a relay restart, short enough that a password reset
# arriving later than that is no longer worth sending.
MAX_DELIVERY_ATTEMPTS = 6
_RETRY_BASE_SECONDS = 60


def normalize_address(address):
    return str(address).strip().lower()


def email_unavailable_reason(*, transactional=True):
    """Why the instance cannot send this kind of mail, or ``None`` if it can.

    Meant for the UI: a feature that would send disables its control and
    shows this instead of queueing a mail that goes nowhere.
    """
    if not settings.EMAIL_ENABLED:
        return "Outgoing email is not configured on this instance."
    if not transactional and not settings.EMAIL_BASE_URL:
        return "EMAIL_BASE_URL is not set, so notification mail cannot carry an unsubscribe link."
    return None


def email_configured(*, transactional=True):
    return email_unavailable_reason(transactional=transactional) is None


def send_email(
    to,
    template,
    *,
    feature,
    context=None,
    user=None,
    transactional=True,
    thread_key=None,
    priority=NORMAL_PRIORITY,
):
    """Render ``template`` for ``to`` and queue its delivery.

    ``template`` names three files: ``<template>.subject.txt``,
    ``<template>.txt`` and ``<template>.html``; the last two usually extend
    ``core/email/base.txt`` and ``core/email/base.html``. ``feature`` says on
    whose behalf the mail goes out - it is what the send record, the
    unsubscribe link and the suppressions are keyed on.

    ``transactional`` is for mail the recipient asked for (a reset link, a
    verification code); anything else carries a one-click unsubscribe and
    stops once the recipient uses it. ``thread_key`` groups every mail about
    the same object into one conversation in the recipient's client.

    Returns the ``EmailDelivery`` - queued, or already suppressed or rate
    limited - or ``None`` when sending is disabled. Raises ``ValueError`` on
    an address that is not one.
    """
    reason = email_unavailable_reason(transactional=transactional)
    if reason is not None:
        logger.info("Email %s to %s not sent: %s", scrub(feature), scrub(to), reason)
        return None

    address = normalize_address(to)
    try:
        validate_email(address)
    except ValidationError as exc:
        raise ValueError(f"Not an email address: {to!r}") from exc

    unsubscribe_url = None if transactional else _unsubscribe_url(address, feature)
    subject, text, html = render_email(
        template,
        {**(context or {}), "unsubscribe_url": unsubscribe_url},
        user=user,
    )
    delivery = EmailDelivery(
        to_address=address,
        user=user,
        feature=feature,
        template=template,
        subject=subject[:255],
        transactional=transactional,
    )

    suppression = _suppression_for(address, feature)
    if suppression is not None:
        delivery.status = EmailDelivery.Status.SUPPRESSED
        delivery.error = f"Address suppressed: {suppression.get_reason_display()}"
        delivery.save()
        logger.info(
            "Email %s to %s suppressed (%s)",
            scrub(feature),
            scrub(address),
            suppression.reason,
        )
        return delivery

    limit = _exceeded_rate_limit(address)
    if limit is not None:
        delivery.status = EmailDelivery.Status.RATE_LIMITED
        delivery.error = limit
        delivery.save()
        logger.warning(
            "Email %s to %s rate limited: %s", scrub(feature), scrub(address), limit
        )
        return delivery

    domain = _sender_domain()
    delivery.message_id = make_msgid(domain=domain)
    delivery.save()

    headers = {
        "Message-ID": delivery.message_id,
        # RFC 3834: nothing here is a human reply, so out-of-office robots
        # must not answer it.
        "Auto-Submitted": "auto-generated",
        "X-Auto-Response-Suppress": "All",
    }
    if thread_key:
        # A parent that is never sent, like the one GitHub puts on every
        # notification about an issue: every mail about the object points at
        # it, so clients thread them without anyone tracking which went first.
        digest = hashlib.sha256(str(thread_key).encode()).hexdigest()[:32]
        root = f"<{digest}@{domain}>"
        headers["References"] = root
        headers["In-Reply-To"] = root
    if unsubscribe_url:
        # RFC 8058 one-click: the client POSTs to the URL itself, and Gmail
        # and Yahoo treat bulk mail without it as spam.
        headers["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    from ..tasks import deliver_email

    delivery_id = str(delivery.uuid)
    transaction.on_commit(
        lambda: deliver_email.apply_async(
            args=[delivery_id],
            kwargs={"subject": subject, "text": text, "html": html, "headers": headers},
            priority=priority,
        )
    )
    return delivery


def render_email(template, context, *, user=None):
    """The subject, text body and HTML body of ``template``, in the
    recipient's language and time zone."""
    context = {
        "site_name": _site_name(),
        "base_url": settings.EMAIL_BASE_URL,
        **context,
    }
    with _recipient_locale(user):
        subject = _render_plain(f"{template}.subject.txt", context)
        text = _render_plain(f"{template}.txt", context)
        html = render_to_string(f"{template}.html", context)
    # A subject is a header: a newline there is a header injection, and
    # Django refuses the whole message over it.
    subject = " ".join(subject.split())
    return subject, text.strip() + "\n", html


def deliver(delivery_id, *, subject, text, html, headers, final_attempt):
    """Hand one queued mail to the relay. Called by the delivery task only.

    Returns ``True`` when the task should retry: the failure was transient
    and attempts remain. Everything else is settled on the record.
    """
    delivery = EmailDelivery.objects.filter(uuid=delivery_id).first()
    if delivery is None or delivery.status != EmailDelivery.Status.QUEUED:
        # Already settled - a duplicate delivery of the task, or a bounce
        # that landed before a retry.
        return False

    suppression = _suppression_for(delivery.to_address, delivery.feature)
    if suppression is not None:
        _settle(
            delivery,
            EmailDelivery.Status.SUPPRESSED,
            f"Address suppressed before delivery: {suppression.get_reason_display()}",
        )
        return False

    EmailDelivery.objects.filter(uuid=delivery.uuid).update(attempts=F("attempts") + 1)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[delivery.to_address],
        reply_to=settings.EMAIL_REPLY_TO or None,
        headers=headers,
    )
    message.attach_alternative(html, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception as exc:
        error = _describe(exc)
        if _is_transient(exc) and not final_attempt:
            EmailDelivery.objects.filter(uuid=delivery.uuid).update(
                error=error, updated_at=timezone.now()
            )
            logger.warning(
                "Email %s to %s failed, will retry: %s",
                delivery.uuid,
                scrub(delivery.to_address),
                scrub(error),
            )
            return True
        _settle(delivery, EmailDelivery.Status.FAILED, error)
        if _is_recipient_rejection(exc):
            suppress_address(
                delivery.to_address,
                EmailSuppression.Reason.HARD_BOUNCE,
                detail=error,
            )
        logger.error(
            "Email %s to %s failed: %s",
            delivery.uuid,
            scrub(delivery.to_address),
            scrub(error),
        )
        return False

    EmailDelivery.objects.filter(uuid=delivery.uuid).update(
        status=EmailDelivery.Status.SENT,
        sent_at=timezone.now(),
        error="",
        provider_message_id=_provider_message_id(message),
        updated_at=timezone.now(),
    )
    logger.info("Email %s sent to %s", delivery.uuid, scrub(delivery.to_address))
    return False


def retry_delay(retries):
    """Seconds before the next delivery attempt, after ``retries`` retries."""
    return _RETRY_BASE_SECONDS * 2**retries


def suppress_address(address, reason, *, feature="", detail=""):
    """Stop mailing ``address`` - for ``feature`` only, or entirely when it is
    empty. Idempotent: the first reason recorded for an address is kept."""
    address = normalize_address(address)
    try:
        with transaction.atomic():
            suppression, _ = EmailSuppression.objects.get_or_create(
                address=address,
                feature=feature,
                defaults={"reason": reason, "detail": detail[:2000]},
            )
    except IntegrityError:
        # Two bounces for the same address racing each other.
        suppression = EmailSuppression.objects.get(address=address, feature=feature)
    return suppression


def record_bounce(address, reason, *, provider_message_id="", detail=""):
    """A hard bounce or a complaint the provider reported after accepting the
    mail: suppress the address, and mark the mail it was about when the
    provider said which."""
    suppression = suppress_address(address, reason, detail=detail)
    if provider_message_id:
        EmailDelivery.objects.filter(
            provider_message_id=provider_message_id,
            to_address=suppression.address,
        ).update(
            status=EmailDelivery.Status.BOUNCED,
            error=f"{suppression.get_reason_display()}: {detail}"[:2000],
            updated_at=timezone.now(),
        )
    logger.info("Email address %s suppressed (%s)", scrub(suppression.address), reason)
    return suppression


def unsubscribe_target(token):
    """The ``(address, feature)`` an unsubscribe token was minted for, or
    ``None`` if it is not one of ours."""
    try:
        payload = signing.loads(token, salt=_UNSUBSCRIBE_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    address, feature = payload.get("a"), payload.get("f")
    if not isinstance(address, str) or not isinstance(feature, str) or not feature:
        return None
    return address, feature


def purge_deliveries(older_than):
    """Delete send records created before ``older_than``; returns the count."""
    deleted, _ = EmailDelivery.objects.filter(created_at__lt=older_than).delete()
    return deleted


def _unsubscribe_url(address, feature):
    token = signing.dumps({"a": address, "f": feature}, salt=_UNSUBSCRIBE_SALT)
    path = reverse("email-unsubscribe", kwargs={"token": token})
    return f"{settings.EMAIL_BASE_URL}{path}"


def _suppression_for(address, feature):
    return (
        EmailSuppression.objects.filter(address=address)
        .filter(Q(feature="") | Q(feature=feature))
        .first()
    )


def _exceeded_rate_limit(address):
    """The limit a new mail to ``address`` would break, or ``None``.

    Counted on the send records, so every worker and web process shares the
    same view. Two sends racing each other may both pass the last free slot;
    the limits are there to cap a runaway loop, not to be exact.
    """
    recent = EmailDelivery.objects.filter(
        created_at__gte=timezone.now() - _RATE_WINDOW,
        status__in=_COUNTED_STATUSES,
    )
    per_recipient = settings.EMAIL_RATE_LIMIT_PER_RECIPIENT
    if recent.filter(to_address=address).count() >= per_recipient:
        return f"Per-recipient limit reached ({per_recipient} per hour)"
    global_limit = settings.EMAIL_RATE_LIMIT_GLOBAL
    if recent.count() >= global_limit:
        return f"Instance limit reached ({global_limit} per hour)"
    return None


def _render_plain(name, context):
    # Text parts and subjects are not HTML: autoescaping would put &amp; and
    # &#x27; in front of the reader.
    return get_template(name).template.render(Context(context, autoescape=False))


@contextmanager
def _recipient_locale(user):
    """Render in the recipient's language and time zone.

    The app has no per-user language yet - every page renders in
    LANGUAGE_CODE - so neither does a mail; the time zone is the user's own
    setting, as on every page they see.
    """
    from workspace.users.services.settings import get_user_timezone

    zone = (
        get_user_timezone(user) if user is not None else timezone.get_default_timezone()
    )
    with translation.override(settings.LANGUAGE_CODE), timezone.override(zone):
        yield


def _sender_domain():
    address = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    return address.rpartition("@")[2] or "localhost"


def _site_name():
    return parseaddr(settings.DEFAULT_FROM_EMAIL)[0] or "Workspace"


def _provider_message_id(message):
    """The id an anymail backend got back for ``message``; empty over SMTP,
    where nothing comes back."""
    status = getattr(message, "anymail_status", None)
    message_id = getattr(status, "message_id", None)
    return message_id if isinstance(message_id, str) else ""


def _settle(delivery, status, error):
    EmailDelivery.objects.filter(uuid=delivery.uuid).update(
        status=status, error=error[:2000], updated_at=timezone.now()
    )


def _describe(exc):
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = ", ".join(
            f"{code} {_decode(message)}" for code, message in exc.recipients.values()
        )
        return f"Recipient refused: {codes}"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"{type(exc).__name__}: {exc.smtp_code} {_decode(exc.smtp_error)}"
    return f"{type(exc).__name__}: {exc}"


def _decode(message):
    if isinstance(message, bytes):
        return message.decode("utf-8", "replace")
    return str(message)


def _is_transient(exc):
    """Whether retrying the same mail later can succeed.

    SMTP says so itself: a 4xx reply is "try again", a 5xx one is final. A
    network error (refused, timeout, TLS) is taken as transient, since a relay
    being restarted looks exactly like that.
    """
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return any(code < 500 for code, _ in exc.recipients.values())
    if isinstance(exc, smtplib.SMTPResponseException):
        return exc.smtp_code < 500
    # Every other SMTPException is an OSError too.
    return isinstance(exc, OSError)


def _is_recipient_rejection(exc):
    """A 5xx on RCPT TO: the relay says the mailbox does not exist, which is
    a hard bounce reported synchronously."""
    return isinstance(exc, smtplib.SMTPRecipientsRefused) and all(
        code >= 500 for code, _ in exc.recipients.values()
    )
