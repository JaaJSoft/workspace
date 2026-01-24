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
