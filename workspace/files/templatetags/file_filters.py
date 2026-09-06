import re

import orjson
from django import template
from django.utils.html import conditional_escape, format_html_join
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def type_to_icon(file_type):
    from workspace.files.services.filetype import get_icon

    return get_icon(file_type or "")


@register.filter
def type_to_color(file_type):
    from workspace.files.services.filetype import get_color

    return get_color(file_type or "")


@register.filter
def thumbnail_url(file_obj):
    """The authenticated API URL for *file_obj*'s thumbnail, or '' without one."""
    if not getattr(file_obj, "has_thumbnail", False):
        return ""
    return f"/api/v1/files/{file_obj.uuid}/thumbnail"


@register.filter
def to_json(value):
    """Serialize a value to JSON for use in HTML attributes.

    Output is auto-escaped by Django (" becomes &quot;), which the
    browser decodes before JavaScript reads dataset.* attributes.
    """
    return orjson.dumps(value).decode()


@register.filter(needs_autoescape=True)
def highlight(text, needle, autoescape=True):
    """Wrap every case-insensitive occurrence of *needle* in *text* in <mark>.

    Both the text and the needle are escaped; only the <mark> tags are
    trusted markup. With an empty needle the escaped text comes back as is.
    """
    text = str(text or "")
    if not needle:
        return conditional_escape(text) if autoescape else text
    parts = re.split(f"({re.escape(str(needle))})", text, flags=re.IGNORECASE)
    needle_lower = str(needle).lower()
    return format_html_join(
        "",
        "{}",
        (
            (
                mark_safe(
                    '<mark class="bg-warning/30 text-inherit rounded-sm">'
                    + conditional_escape(part)
                    + "</mark>"
                )
                if part.lower() == needle_lower
                else part,
            )
            for part in parts
            if part
        ),
    )
