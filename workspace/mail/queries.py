from .models import MailAccount


def user_account_ids(user):
    """Return mail account UUIDs owned by the user."""
    return MailAccount.objects.filter(owner=user).values_list('uuid', flat=True)
