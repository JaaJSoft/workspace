from urllib.parse import urlencode

from django import template

register = template.Library()


@register.filter
def clip_duration(seconds):
    """A video length as a player shows it: "0:07", "12:34", "1:02:03"."""
    total = max(round(seconds), 1)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


@register.filter
def files_url(file_obj):
    """Where Files shows *file_obj*, with its viewer open.

    The file's folder when the user can browse it - their own files and their
    groups' - and the Shared with me listing otherwise: a file shared on its
    own sits in a folder that belongs to someone else. Reads the
    ``in_browsable_folder`` annotation of ``timeline.with_timeline_fields``.
    """
    if file_obj.in_browsable_folder:
        folder = f"/files/{file_obj.parent_id}" if file_obj.parent_id else "/files"
        return f"{folder}?{urlencode({'open': file_obj.uuid})}"
    return f"/files?{urlencode({'shared': 1, 'open': file_obj.uuid})}"
