def on_user_groups_changed(sender, instance, action, **kwargs):
    """Invalidate the module-access cache when a user's groups change."""
    if action in ("post_add", "post_remove", "post_clear"):
        from workspace.core.services.module_access import (
            invalidate_module_access_cache,
        )

        invalidate_module_access_cache()
