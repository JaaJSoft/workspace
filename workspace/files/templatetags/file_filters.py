import orjson

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def mime_to_lucide(mime_type):
    """Convert MIME type to Lucide icon name."""
    from workspace.files.services.mime import get_icon
    return get_icon(mime_type)


@register.filter
def mime_to_color(mime_type):
    """Convert MIME type to Tailwind color class."""
    from workspace.files.services.mime import get_color
    return get_color(mime_type)


@register.filter
def to_json(value):
    """Serialize a value to JSON for use in HTML attributes.

    Output is auto-escaped by Django (" becomes &quot;), which the
    browser decodes before JavaScript reads dataset.* attributes.
    """
    return orjson.dumps(value).decode()
