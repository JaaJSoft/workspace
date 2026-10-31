"""Notifying the people a file share reaches."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import translation

from workspace.users.services.settings import get_user_language, in_recipient_languages


def file_url(file_obj):
    """Where a notification about *file_obj* lands: its folder."""
    return f"/files/{file_obj.parent_id}" if file_obj.parent_id else "/files"


def notify_share_targets(
    file_obj,
    *,
    target_user=None,
    target_group=None,
    target_project=None,
    title,
    actor,
    url=None,
):
    """Notify the user, or every active member of the group or project but
    *actor*. *title* is a callable returning the title, called in each
    recipient's language."""
    from workspace.notifications.services.notifications import notify, notify_many
    from workspace.projects.queries import project_users

    if url is None:
        url = file_url(file_obj)
    if target_user is not None:
        with translation.override(get_user_language(target_user)):
            notify(
                recipient=target_user,
                origin="files",
                title=title(),
                url=url,
                actor=actor,
                source=file_obj,
            )
        return
    if target_project is not None:
        recipients = [
            u for u in project_users(target_project) if u.is_active and u.pk != actor.pk
        ]
    else:
        recipients = list(
            get_user_model()
            .objects.filter(groups=target_group, is_active=True)
            .exclude(pk=actor.pk)
        )
    with transaction.atomic():
        for users in in_recipient_languages(recipients):
            notify_many(
                recipients=users,
                origin="files",
                title=title(),
                url=url,
                actor=actor,
                source=file_obj,
            )
