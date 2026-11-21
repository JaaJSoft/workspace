"""Senders a user lets load remote images in their messages."""

from ..models import MailTrustedSender


def _normalize(email):
    return (email or "").strip().lower()


def is_trusted_sender(user_id, email):
    email = _normalize(email)
    if not email:
        return False
    return MailTrustedSender.objects.filter(owner_id=user_id, email=email).exists()


def trust_sender(user, email):
    """Trust ``email`` for ``user``. Returns False for an empty address."""
    email = _normalize(email)
    if not email:
        return False
    MailTrustedSender.objects.get_or_create(owner=user, email=email)
    return True


def distrust_sender(user, email):
    MailTrustedSender.objects.filter(owner=user, email=_normalize(email)).delete()
