from django.db.models.signals import post_save
from django.dispatch import receiver

# The descriptions are read by the AI classifier, so they are phrased as
# matching criteria rather than as a gloss of the label name.
DEFAULT_LABELS = [
    {
        "name": "Urgent",
        "description": (
            "Needs an answer or a decision today: a deadline, an incident, "
            "someone waiting on you."
        ),
        "color": "error",
        "icon": "alert-triangle",
        "position": 0,
        "notify_on_apply": True,
    },
    {
        "name": "Action",
        "description": (
            "Asks you to do something, but not today: a task, a form to fill, "
            "a reply that can wait."
        ),
        "color": "warning",
        "icon": "check-circle",
        "position": 1,
    },
    {
        "name": "FYI",
        "description": (
            "Written to you personally but needs nothing back: an update, "
            "a confirmation, a heads-up."
        ),
        "color": "info",
        "icon": "info",
        "position": 2,
    },
    {
        "name": "Newsletter",
        "description": (
            "Editorial mail you subscribed to: newsletters, digests, blog posts, "
            "marketing campaigns."
        ),
        "color": "secondary",
        "icon": "newspaper",
        "position": 3,
    },
    {
        "name": "Notification",
        "description": (
            "Automated mail from a service you use: receipts, alerts, "
            "password resets, build results."
        ),
        "color": "ghost",
        "icon": "bell",
        "position": 4,
    },
    {
        "name": "Suspicious",
        "description": (
            "Looks like phishing, a scam or spam: a forged sender, an urgent "
            "payment or credential request, an unsolicited offer."
        ),
        "color": "error",
        "icon": "shield",
        "position": 5,
    },
]


@receiver(post_save, sender="mail.MailAccount")
def seed_default_labels(sender, instance, created, raw=False, using=None, **kwargs):
    # `raw=True` means we're inside loaddata; the fixture already carries
    # the labels (or doesn't, by design) and the DB may not be in a
    # consistent state for related-object creation.
    if raw or not created:
        return
    from workspace.mail.models import MailLabel

    MailLabel.objects.using(using).bulk_create(
        [MailLabel(account=instance, **label_data) for label_data in DEFAULT_LABELS]
    )
