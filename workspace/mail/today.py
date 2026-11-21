"""The mail's source for the dashboard's Today card."""

from django.db.models import Exists, OuterRef, Prefetch, Q

from workspace.core.today_registry import capped_rows
from workspace.mail.models import MailFolder, MailMessage, MailMessageLabel
from workspace.mail.queries import user_account_ids

MAIL_LIMIT = 5


def important_mail(user, now, tz):
    """The messages that wait on the user, newest first.

    Two kinds: unread mail carrying a label that notifies on apply (the
    default *Urgent* label is one), and starred mail, which IMAP calls
    ``\\Flagged`` and most clients present as "follow up", read or not.
    Trash and Spam are left out. Each row carries ``notify_labels``.
    """
    notify_links = MailMessageLabel.objects.filter(label__notify_on_apply=True)
    has_notify_label = Exists(notify_links.filter(message=OuterRef("pk")))
    messages = (
        MailMessage.objects.filter(
            account_id__in=user_account_ids(user),
            deleted_at__isnull=True,
            is_draft=False,
        )
        .exclude(
            folder__folder_type__in=[
                MailFolder.FolderType.TRASH,
                MailFolder.FolderType.SPAM,
            ]
        )
        .filter(Q(is_starred=True) | Q(has_notify_label, is_read=False))
        # A row shows the envelope only; bodies can weigh megabytes.
        .only("subject", "from_name", "from_email", "date", "is_read", "is_starred")
        .prefetch_related(
            Prefetch(
                "message_labels",
                queryset=notify_links.select_related("label"),
                to_attr="notify_labels",
            )
        )
        .order_by("-date")
    )
    return capped_rows(messages, MAIL_LIMIT)
