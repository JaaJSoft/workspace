"""Group membership hooks, wired in ``apps.FilesConfig.ready()``."""


def revoke_sharing_on_group_leave(sender, instance, action, pk_set, reverse, **kwargs):
    """Revoke the public links and shares a user made on a group's nodes when
    they leave it, and drop their pins there.

    ``clear()`` is handled before it runs: once the rows are gone, nothing says
    which memberships it removed.
    """
    from .services.pins import remove_group_pins
    from .services.share_notifications import notify_share_targets
    from .services.sharing import revoke_group_share_links, revoke_group_shares

    if action == "post_remove":
        members = list(pk_set or ())
        if reverse:
            user_ids, group_ids = members, [instance.pk]
        else:
            user_ids, group_ids = [instance.pk], members
    elif action == "pre_clear":
        if reverse:
            user_ids = list(
                sender.objects.filter(group_id=instance.pk).values_list(
                    "user_id", flat=True
                )
            )
            group_ids = [instance.pk]
        else:
            user_ids = [instance.pk]
            group_ids = list(instance.groups.values_list("pk", flat=True))
    else:
        return
    if not (user_ids and group_ids):
        return
    revoke_group_share_links(user_ids=user_ids, group_ids=group_ids)
    remove_group_pins(user_ids=user_ids, group_ids=group_ids)
    for share in revoke_group_shares(user_ids=user_ids, group_ids=group_ids):
        notify_share_targets(
            share.file,
            target_user=share.shared_with,
            target_group=share.shared_with_group,
            target_project=share.shared_with_project,
            actor=share.shared_by,
            title=f'"{share.file.name}" is no longer shared with you',
            url="",
        )
