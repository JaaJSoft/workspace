"""Keep usernames that would break storage paths out of the user table.

Connected in UsersConfig.ready().
"""

from django.contrib.auth.models import User
from django.db.models.signals import pre_save
from django.dispatch import receiver

from .validators import PATH_SEGMENT_USERNAMES, validate_username


@receiver(pre_save, sender=User)
def refuse_path_segment_username(sender, instance, using, update_fields, **kwargs):
    """Refuse to create a user under such a name, or to rename one to it.

    Every way an account comes to be goes through here - the admin, OIDC
    provisioning, ``createsuperuser``, ``create_bot`` - which a form validator
    alone would not cover. An account named so before the rule existed keeps
    saving: refusing it would lock its owner out rather than protect anything.
    """
    if instance.username not in PATH_SEGMENT_USERNAMES:
        return
    if update_fields is not None and "username" not in update_fields:
        return
    if not instance._state.adding:
        stored = (
            sender._default_manager.using(using)
            .filter(pk=instance.pk)
            .values_list("username", flat=True)
            .first()
        )
        if stored == instance.username:
            return
    validate_username(instance.username)
