from workspace.core.sse_registry import MailboxSSEProvider, push_user_event

SLUG = "files"


class FilesSSEProvider(MailboxSSEProvider):
    slug = SLUG


def push_file_event(file_obj, event_type, actor_username, exclude_user_id=None):
    """Push an SSE event to every user who can reach this file.

    Every access branch of ``FileService._access_branches`` has to be here:
    a file in a group folder reaches its group members through the group,
    with no ``FileShare`` row naming them, a group share reaches them through
    the group it names, a project share through the project's members, and
    group folders are the main place two people open the same note. Leaving
    that branch out left their editors read-only after a ``lock_released``
    they never received.
    """
    from django.contrib.auth import get_user_model

    from workspace.files.models import FileShare
    from workspace.projects.queries import project_users

    shares = FileShare.objects.filter(file=file_obj)
    user_ids = {file_obj.owner_id}
    user_ids.update(
        shares.filter(shared_with__isnull=False).values_list(
            "shared_with_id", flat=True
        )
    )
    group_ids = set(
        shares.filter(shared_with_group__isnull=False).values_list(
            "shared_with_group_id", flat=True
        )
    )
    if file_obj.group_id:
        group_ids.add(file_obj.group_id)
    if group_ids:
        user_ids.update(
            get_user_model()
            .objects.filter(groups__in=group_ids)
            .values_list("pk", flat=True)
        )
    for share in shares.filter(shared_with_project__isnull=False).select_related(
        "shared_with_project"
    ):
        user_ids.update(u.pk for u in project_users(share.shared_with_project))

    if exclude_user_id:
        user_ids.discard(exclude_user_id)

    event = {
        "type": event_type,
        "file_uuid": str(file_obj.uuid),
        "actor": actor_username,
    }

    for uid in user_ids:
        push_user_event(SLUG, uid, event)
