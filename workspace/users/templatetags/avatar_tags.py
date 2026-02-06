from django import template

from workspace.users.avatar_service import has_avatar as _has_avatar

register = template.Library()


@register.simple_tag
def avatar_url(user):
    """Return the avatar API URL if the user has an avatar, else empty string."""
    if _has_avatar(user):
        return f"/api/v1/users/{user.id}/avatar"
    return ""


@register.filter
def has_avatar(user):
    """Return True if the user has an uploaded avatar."""
    return _has_avatar(user)
