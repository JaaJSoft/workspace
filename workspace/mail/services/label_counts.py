"""Helpers for refreshing denormalized MailLabel.unread_count.

Use these from any code path that mutates ``MailMessage.is_read`` in bulk
(folder mark-as-read, batch actions, IMAP sync flag reconciliation). Without
propagating the change to ``MailLabel.unread_count``, sidebar badges go stale
until another action forces a recompute.
"""


def refresh_labels_for_messages(message_ids):
    """Refresh unread_count for every label attached to any of these messages.

    Accepts an iterable of MailMessage PKs. No-op when empty.
    """
    if not message_ids:
        return
    from ..models import MailLabel, MailMessageLabel

    label_ids = set(
        MailMessageLabel.objects.filter(message_id__in=message_ids).values_list(
            "label_id", flat=True
        )
    )
    if not label_ids:
        return
    # Deferred import to avoid a load-time cycle: views.py imports from
    # workspace.mail.services.* in places. Once _refresh_label_counts is
    # itself moved into services (follow-up), this can become a top-level
    # import.
    from ..views import _refresh_label_counts

    _refresh_label_counts(MailLabel.objects.filter(pk__in=label_ids))
