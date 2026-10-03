"""Bounces and complaints the mail provider reports through anymail's webhooks."""

from anymail.signals import EventType, RejectReason, tracking
from django.dispatch import receiver

from workspace.core.models import EmailSuppression
from workspace.core.services.email import record_bounce

# A provider that refuses to send because it already saw the address bounce
# (or never saw a valid one) is reporting the same hard bounce, only later.
_BOUNCE_REJECTIONS = {RejectReason.BOUNCED, RejectReason.INVALID}


def _is_permanent_bounce(event):
    """Anymail files every non-delivery the receiving server reported as
    "bounced", and some providers report the temporary ones too: Postmark's
    SoftBounce (a full mailbox) arrives as one. So does a message refused for
    carrying a virus, which says nothing about the address."""
    if event.reject_reason == RejectReason.OTHER:
        return False
    esp_event = event.esp_event if isinstance(event.esp_event, dict) else {}
    return esp_event.get("Type") != "SoftBounce"


def _suppression_reason(event):
    if event.event_type == EventType.BOUNCED:
        if not _is_permanent_bounce(event):
            return None
        return EmailSuppression.Reason.HARD_BOUNCE
    if event.event_type == EventType.COMPLAINED:
        return EmailSuppression.Reason.COMPLAINT
    if (
        event.event_type == EventType.REJECTED
        and event.reject_reason in _BOUNCE_REJECTIONS
    ):
        return EmailSuppression.Reason.HARD_BOUNCE
    # Deferred mail is the provider's to retry; an unsubscribe at the
    # provider's level is not ours to act on - ours goes through our own link.
    return None


@receiver(tracking)
def suppress_bounced_addresses(sender, event, esp_name, **kwargs):
    reason = _suppression_reason(event)
    if reason is None or not event.recipient:
        return
    detail = " - ".join(
        part for part in (esp_name, event.description, event.mta_response) if part
    )
    record_bounce(
        event.recipient,
        reason,
        provider_message_id=event.message_id or "",
        detail=detail,
    )
