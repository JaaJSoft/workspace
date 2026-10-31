"""Event and poll notifications, written in each recipient's language."""

from django.db import transaction

from workspace.notifications.services.notifications import notify_many
from workspace.users.services.settings import in_recipient_languages


def notify_in_languages(*, recipients, message, **kwargs):
    """``notify_many`` from the calendar, in each recipient's language.

    A notification is stored as text, so *message* - a callable returning the
    ``(title, body)`` pair - is called once per language, with that language
    active. All-or-nothing: a failure in a later language group rolls back the
    earlier ones, so a retry does not notify them twice.
    """
    with transaction.atomic():
        for users in in_recipient_languages(recipients):
            title, body = message()
            notify_many(
                recipients=users, origin="calendar", title=title, body=body, **kwargs
            )
