"""Group membership hooks, wired in ``apps.FilesConfig.ready()``."""


def revoke_links_on_group_leave(sender, instance, action, pk_set, reverse, **kwargs):
    """Revoke the public links a user made on a group's nodes when they leave it.

    ``clear()`` is handled before it runs: once the rows are gone, nothing says
    which memberships it removed.
    """
    from .services.sharing import revoke_group_share_links

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
    if user_ids and group_ids:
        revoke_group_share_links(user_ids=user_ids, group_ids=group_ids)
