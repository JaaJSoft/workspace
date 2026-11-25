from django import template

from workspace.users.services.presence import get_manual_status

register = template.Library()


@register.filter
def manual_status(user):
    """The status the user picked in the user menu ('auto' when none)."""
    return get_manual_status(user.pk)
