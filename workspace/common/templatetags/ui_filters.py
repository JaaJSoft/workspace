from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def gt(value, arg):
    """Return True if value > arg. Both are cast to int."""
    try:
        return int(value) > int(arg)
    except ValueError, TypeError:
        return False


@register.filter
def localtime_tag(value, fmt="time"):
    """Render a ``<time>`` element that JS converts to the user's local timezone.

    Supported formats (passed as the filter argument):
      - ``time``      → HH:MM  (default)
      - ``date``      → "Today", "Yesterday", or "Feb 5"
      - ``datetime``  → "Feb 5, 2:30 PM"
      - ``smart``     → HH:MM today, "Feb 5, 2:30 PM" otherwise
      - ``relative``  → "5 minutes ago"
      - ``full``      → "Feb 5, 2025 · 2:30 PM"

    Usage::

        {{ msg.created_at|localtime_tag }}
        {{ msg.created_at|localtime_tag:"date" }}
        {{ msg.created_at|localtime_tag:"datetime" }}
    """
    if not value:
        return ""
    iso = value.isoformat()
    # Server-side UTC fallback displayed until JS upgrades the element
    from django.utils import timezone

    local = timezone.localtime(value)
    fallback = local.strftime("%H:%M")
    return mark_safe(f'<time datetime="{iso}" data-localtime="{fmt}">{fallback}</time>')
