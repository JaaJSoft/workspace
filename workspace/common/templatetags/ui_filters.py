from django import template

register = template.Library()


@register.filter
def gt(value, arg):
    """Return True if value > arg. Both are cast to int."""
    try:
        return int(value) > int(arg)
    except (ValueError, TypeError):
        return False
