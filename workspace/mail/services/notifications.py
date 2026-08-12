"""Which arriving mail produces a notification, and how many at a time.

Two entry points rather than one, because a message's AI labels do not exist
when it is synced: ``notify_new_messages`` runs inside the IMAP sync,
``notify_labeled_messages`` runs once the classifier has committed its labels.
"""

import logging

from django.db.models import F

from workspace.common.logging import scrub
from workspace.notifications.services.notifications import mark_sources_read, notify
from workspace.users.services.settings import get_module_settings

from ..models import MailMessage
from .ai_settings import is_mail_ai_feature_enabled

logger = logging.getLogger(__name__)

NOTIFY_MODES = ("all", "labels", "never")
DEFAULT_NOTIFY_BURST = 10
HARD_MAX_NOTIFY_BURST = 50

# A message we sent or drafted is not news to its sender.
_SILENT_FOLDER_TYPES = ("sent", "drafts")


def _classifier_available(user) -> bool:
    from workspace.ai.client import is_ai_enabled

    return is_ai_enabled() and is_mail_ai_feature_enabled(user, "classify")


def resolve_notify_mode(user) -> str:
    """The user's mail notification mode.

    Absent or unrecognised values resolve to a computed default rather than a
    stored one, so turning the classifier off also turns off a default that
    depends on it instead of leaving a mode selected that can never fire.
    """
    mode = get_module_settings(user, "mail").get("notify_mode")
    if mode in NOTIFY_MODES:
        return mode
    return "labels" if _classifier_available(user) else "never"


def resolve_notify_burst(user) -> int:
    """Max notifications per folder per sync, bounded whatever is stored.

    Clamped here rather than at the API boundary: the settings endpoint stores
    arbitrary JSON, and fixtures, management commands and direct ORM writes
    never pass through a view at all. This value is read on the IMAP sync path,
    so it is bounded where it is consumed.
    """
    raw = get_module_settings(user, "mail").get("notify_max_burst")
    try:
        value = int(raw)
    except TypeError, ValueError, OverflowError:
        return DEFAULT_NOTIFY_BURST
    return max(1, min(value, HARD_MAX_NOTIFY_BURST))


def _is_notifiable_folder(folder) -> bool:
    return folder.folder_type not in _SILENT_FOLDER_TYPES and not folder.is_hidden


def _notify_messages(user, messages, *, priority) -> int:
    for message in messages:
        notify(
            recipient=user,
            origin="mail",
            title=message.from_name or message.from_email or "New email",
            body=message.subject or "(no subject)",
            url=f"/mail?message={message.uuid}",
            priority=priority,
            source=message,
        )
    return len(messages)


def notify_new_messages(folder, message_uuids, *, was_initial_sync) -> int:
    """Notify for messages that just landed in *folder*. Mode "all" only."""
    if was_initial_sync or not message_uuids:
        return 0
    if not _is_notifiable_folder(folder):
        return 0
    user = folder.account.owner
    if resolve_notify_mode(user) != "all":
        return 0

    limit = resolve_notify_burst(user)
    qualifying = list(
        MailMessage.objects.filter(
            uuid__in=message_uuids, is_read=False, deleted_at__isnull=True
        )
        # NULLs sort last on Postgres by default but first on SQLite; both are
        # production backends here, so the ordering must be forced explicitly.
        .order_by(F("date").desc(nulls_last=True))
        .only("uuid", "subject", "from_name", "from_email")
    )
    total = len(qualifying)
    if not total:
        return 0
    messages = qualifying[:limit]
    if total > limit:
        logger.info(
            "Mail notifications capped in %s: %d of %d notified",
            scrub(folder.name),
            limit,
            total,
        )
    return _notify_messages(user, messages, priority="normal")


def notify_labeled_messages(user, messages, *, was_initial_sync) -> int:
    """Notify for messages the classifier just gave a notifying label.

    *messages* must already be filtered to those carrying such a label, and
    must come with their ``folder`` selected: this reads folder_type and
    is_hidden per message.
    """
    if was_initial_sync or not messages:
        return 0
    if resolve_notify_mode(user) != "labels":
        return 0

    eligible = [
        m for m in messages if not m.is_read and _is_notifiable_folder(m.folder)
    ]
    if not eligible:
        return 0
    limit = resolve_notify_burst(user)
    if len(eligible) > limit:
        logger.info(
            "Mail label notifications capped for user %s: %d of %d notified",
            user.pk,
            limit,
            len(eligible),
        )
    return _notify_messages(user, eligible[:limit], priority="high")


def clear_notifications_for_deleted_messages(user, message_pks) -> int:
    """Mark read the push notifications for messages that just got soft-deleted.

    Deletion here never CASCADEs: every mail queryset filters
    ``deleted_at__isnull=True``, so a deleted message can never again appear
    on a rendered page for ``mark_sources_read`` to catch. Called from
    reconciliation (another IMAP client deleted or moved the message) and
    from the in-app delete paths. Lightweight, unsaved ``MailMessage``
    instances stand in for the deleted rows, mirroring the pattern already
    used for a single source in ``chat/views_messages.py``.
    """
    message_pks = list(message_pks)
    if not message_pks:
        return 0
    return mark_sources_read(user, [MailMessage(pk=pk) for pk in message_pks])
