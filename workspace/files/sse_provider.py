from workspace.core.sse_registry import MailboxSSEProvider, push_user_event

SLUG = "files"


class FilesSSEProvider(MailboxSSEProvider):
    slug = SLUG


def push_file_event(file_obj, event_type, actor_username, exclude_user_id=None):
    """Push an SSE event to every user who can reach this file.

    The three access branches of ``FileService._access_branches`` all have to
    be here: a file in a group folder reaches its group members through the
    group, with no ``FileShare`` row to enumerate, and group folders are the
    main place two people open the same note. Leaving that branch out left
    their editors read-only after a ``lock_released`` they never received.
    """
    from django.contrib.auth import get_user_model

    from workspace.files.models import FileShare

    user_ids = {file_obj.owner_id}
    shared_ids = FileShare.objects.filter(
        file=file_obj,
    ).values_list("shared_with_id", flat=True)
    user_ids.update(shared_ids)

    if file_obj.group_id:
        user_ids.update(
            get_user_model()
            .objects.filter(groups__id=file_obj.group_id)
            .values_list("pk", flat=True)
        )

    if exclude_user_id:
        user_ids.discard(exclude_user_id)

    event = {
        "type": event_type,
        "file_uuid": str(file_obj.uuid),
        "actor": actor_username,
    }

    for uid in user_ids:
        push_user_event(SLUG, uid, event)
