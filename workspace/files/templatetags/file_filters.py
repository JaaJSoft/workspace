import orjson

from django import template

register = template.Library()


@register.filter
def type_to_icon(file_type):
    from workspace.files.services.filetype import get_icon
    return get_icon(file_type or '')


@register.filter
def type_to_color(file_type):
    from workspace.files.services.filetype import get_color
    return get_color(file_type or '')


@register.filter
def to_json(value):
    """Serialize a value to JSON for use in HTML attributes.

    Output is auto-escaped by Django (" becomes &quot;), which the
    browser decodes before JavaScript reads dataset.* attributes.
    """
    return orjson.dumps(value).decode()
