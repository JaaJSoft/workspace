from urllib.parse import urlencode

from django import template
from django.db.models import F
from django.urls import reverse

from workspace.photos.queries import person_photos

register = template.Library()


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


@register.simple_tag
def photos_of_person(user, person, limit=8):
    """The newest photos of *person* in *user*'s library, for the People page.

    ``{"photos": [...], "count": n, "url": ...}``: only the user's own
    clusters count, so a shared contact shows each viewer their own photos.
    """
    photos = person_photos(user, person)
    return {
        "photos": list(
            photos.order_by(
                F("media_item__taken_at").desc(nulls_last=True), "-created_at"
            )[:limit]
        ),
        "count": photos.count(),
        "url": f"{reverse('photos_ui:index')}?{urlencode({'person': person.pk})}",
    }
