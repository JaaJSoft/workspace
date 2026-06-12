from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from workspace.common.uuids import uuid_v7_or_v4


class ModuleAccessRule(models.Model):
    """Access rule for a workspace module, global or scoped to a group.

    A rule with ``group=None`` is the single global rule for that module.
    Group-scoped rules override the global rule for that group's members.
    Resolution lives in ``workspace.core.services.module_access``.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    module_slug = models.CharField(max_length=64)
    group = models.ForeignKey(
        "auth.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="module_access_rules",
    )
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["module_slug", "group"],
                name="unique_module_group_rule",
            ),
            models.UniqueConstraint(
                fields=["module_slug"],
                condition=Q(group__isnull=True),
                name="unique_global_module_rule",
            ),
        ]

    def __str__(self):
        scope = self.group.name if self.group_id else "global"
        state = "enabled" if self.is_enabled else "disabled"
        return f"{self.module_slug} [{scope}]: {state}"

    def clean(self):
        # Imported here to avoid a circular import (the service imports the model).
        from workspace.core.services.module_access import restrictable_module_slugs

        if self.module_slug not in restrictable_module_slugs():
            raise ValidationError(
                {"module_slug": "Unknown or non-restrictable module."}
            )


@receiver(post_save, sender=ModuleAccessRule)
@receiver(post_delete, sender=ModuleAccessRule)
def _invalidate_module_access_cache(sender, **kwargs):
    from workspace.core.services.module_access import invalidate_module_access_cache

    invalidate_module_access_cache()
