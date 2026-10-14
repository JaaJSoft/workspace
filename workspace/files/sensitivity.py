"""Files whose preview stays out of sight until the viewer asks for it.

Files blurs the thumbnail, covers the viewer and offers the toggle, but does
not decide what is sensitive: the module that stores the flag (photos, on its
MediaItem rows) plugs in from its ``AppConfig.ready()``, so the dependency
points at ``files`` and never back. Without a provider nothing is sensitive
and nothing can be marked.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db.models import BooleanField, Value

from .models import File


@dataclass(frozen=True)
class SensitivityProvider:
    # An expression over a File row (OuterRef("pk")): True or False for a
    # file that can be marked, null for one that cannot.
    flag: Callable
    # Marks the files of the given uuids sensitive (True) or not (False);
    # returns how many it marked.
    mark: Callable


_provider = None


def register_sensitivity_provider(provider):
    global _provider
    _provider = provider


def with_sensitivity(files_qs):
    """*files_qs* annotated with ``is_sensitive``: True, False, or None for a
    file that cannot be marked."""
    if _provider is None:
        return files_qs.annotate(
            is_sensitive=Value(None, output_field=BooleanField(null=True))
        )
    return files_qs.annotate(is_sensitive=_provider.flag())


def is_sensitive(file_obj):
    """Whether *file_obj*'s preview is kept out of sight; None when the file
    cannot be marked."""
    if hasattr(file_obj, "is_sensitive"):
        return file_obj.is_sensitive
    return (
        with_sensitivity(File.objects.filter(pk=file_obj.pk))
        .values_list("is_sensitive", flat=True)
        .first()
    )


def mark_sensitive(file_uuids, sensitive):
    """Mark the files *file_uuids* sensitive or not; returns how many."""
    if _provider is None:
        return 0
    return _provider.mark(list(file_uuids), sensitive)
