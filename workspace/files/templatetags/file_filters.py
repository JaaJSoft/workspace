from django import template

register = template.Library()


@register.filter
def filesize(size_bytes):
    """Format size in bytes to human readable string."""
    if size_bytes is None:
        return '-'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            if unit == 'B':
                return f"{size_bytes} {unit}"
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


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
