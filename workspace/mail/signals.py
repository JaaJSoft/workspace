from django.db.models.signals import post_save
from django.dispatch import receiver

DEFAULT_LABELS = [
    {'name': 'Urgent', 'color': 'error', 'icon': 'alert-triangle', 'position': 0},
    {'name': 'Action', 'color': 'warning', 'icon': 'check-circle', 'position': 1},
    {'name': 'FYI', 'color': 'info', 'icon': 'info', 'position': 2},
    {'name': 'Newsletter', 'color': 'secondary', 'icon': 'newspaper', 'position': 3},
    {'name': 'Notification', 'color': 'ghost', 'icon': 'bell', 'position': 4},
]

@receiver(post_save, sender='mail.MailAccount')
def seed_default_labels(sender, instance, created, **kwargs):
    if not created:
        return
    from workspace.mail.models import MailLabel
    MailLabel.objects.bulk_create([
        MailLabel(account=instance, **label_data)
        for label_data in DEFAULT_LABELS
    ])
