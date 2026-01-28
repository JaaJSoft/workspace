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
    if not mime_type:
        return 'file'

    mime_type = mime_type.lower()

    # Images
    if mime_type.startswith('image/'):
        return 'image'

    # Videos
    if mime_type.startswith('video/'):
        return 'video'

    # Audio
    if mime_type.startswith('audio/'):
        return 'music'

    # Text/code
    if mime_type.startswith('text/'):
        if 'html' in mime_type:
            return 'file-code'
        if 'css' in mime_type:
            return 'file-code'
        if 'javascript' in mime_type or 'typescript' in mime_type:
            return 'file-code'
        if 'markdown' in mime_type:
            return 'file-text'
        return 'file-text'

    # Applications
    if mime_type.startswith('application/'):
        if 'pdf' in mime_type:
            return 'file-text'
        if 'json' in mime_type:
            return 'file-json'
        if 'xml' in mime_type:
            return 'file-code'
        if 'zip' in mime_type or 'tar' in mime_type or 'gzip' in mime_type or 'rar' in mime_type:
            return 'file-archive'
        if 'javascript' in mime_type or 'typescript' in mime_type:
            return 'file-code'
        if 'spreadsheet' in mime_type or 'excel' in mime_type:
            return 'file-spreadsheet'
        if 'document' in mime_type or 'word' in mime_type:
            return 'file-text'
        if 'presentation' in mime_type or 'powerpoint' in mime_type:
            return 'file-presentation'

    return 'file'


@register.filter
def mime_to_color(mime_type):
    """Convert MIME type to Tailwind color class."""
    if not mime_type:
        return 'text-base-content/60'

    mime_type = mime_type.lower()

    # Images
    if mime_type.startswith('image/'):
        return 'text-success'

    # Videos
    if mime_type.startswith('video/'):
        return 'text-error'

    # Audio
    if mime_type.startswith('audio/'):
        return 'text-secondary'

    # PDF
    if 'pdf' in mime_type:
        return 'text-error'

    # Archives
    if 'zip' in mime_type or 'tar' in mime_type or 'gzip' in mime_type or 'rar' in mime_type:
        return 'text-warning'

    # Code files
    if mime_type.startswith('text/') or 'json' in mime_type or 'xml' in mime_type or 'javascript' in mime_type:
        return 'text-info'

    return 'text-base-content/60'
