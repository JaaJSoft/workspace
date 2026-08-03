"""File comment audience and notification fan-out."""

from django.contrib.auth import get_user_model

from workspace.common.services.mentions import mentioned_users, newly_mentioned_users
from workspace.notifications.services.notifications import notify_stream

from ..models import FileComment

User = get_user_model()


def mentionable_users(file_obj):
    """Users who can see *file_obj*: owner, group members, share targets.

    Mirrors the access branches of ``FileService`` (owned / group / shared);
    keep the two in sync. Sorted by username for stable autocomplete lists.
    """
    users = {}
    if file_obj.owner.is_active:
        users[file_obj.owner_id] = file_obj.owner
    if file_obj.group_id:
        for user in User.objects.filter(groups=file_obj.group_id, is_active=True):
            users.setdefault(user.pk, user)
    for share in file_obj.shares.filter(shared_with__is_active=True).select_related(
        "shared_with"
    ):
        users.setdefault(share.shared_with_id, share.shared_with)
    return sorted(users.values(), key=lambda u: u.username.lower())


def _file_url(file_obj):
    return f"/files/{file_obj.parent_id}" if file_obj.parent_id else "/files"


def _notify_mentioned(file_obj, actor, mentioned):
    notify_stream(
        recipient_ids=[u.pk for u in mentioned],
        source=file_obj,
        origin="files",
        title=f'{actor.username} mentioned you in a comment on "{file_obj.name}"',
        url=_file_url(file_obj),
        actor=actor,
        default_priority="high",
    )


def notify_comment_added(file_obj, actor, body, *, audience=None):
    """Notify about a new comment.

    Audience members mentioned in *body* get a high-priority mention
    notification; the owner and prior commenters get the regular one.
    """
    if audience is None:
        audience = mentionable_users(file_obj)
    mentioned = mentioned_users(audience, body, actor)
    if mentioned:
        _notify_mentioned(file_obj, actor, mentioned)
    mentioned_ids = {u.pk for u in mentioned}

    recipients = set()
    if file_obj.owner != actor:
        recipients.add(file_obj.owner)
    commenter_ids = (
        FileComment.objects.filter(file=file_obj, deleted_at__isnull=True)
        .exclude(author=actor)
        .values_list("author", flat=True)
        .distinct()
    )
    recipients.update(User.objects.filter(pk__in=commenter_ids))
    recipients = [u for u in recipients if u.pk not in mentioned_ids]
    if recipients:
        notify_stream(
            recipient_ids=[u.pk for u in recipients],
            source=file_obj,
            origin="files",
            title=f'{actor.username} commented on "{file_obj.name}"',
            url=_file_url(file_obj),
            actor=actor,
        )


def notify_comment_edited(file_obj, actor, old_body, new_body, *, audience=None):
    """Notify only audience members newly mentioned by the edit."""
    if audience is None:
        audience = mentionable_users(file_obj)
    newly_mentioned = newly_mentioned_users(audience, actor, old_body, new_body)
    if newly_mentioned:
        _notify_mentioned(file_obj, actor, newly_mentioned)
