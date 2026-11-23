from django import template

from workspace.users.services.avatar import has_avatar as _has_avatar

register = template.Library()


@register.filter
def has_avatar(user):
    """Return True if the user has an uploaded avatar."""
    return _has_avatar(user)
