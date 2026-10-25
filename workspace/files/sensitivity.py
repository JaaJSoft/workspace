"""Files whose preview stays out of sight until the viewer asks for it.

Files blurs the thumbnail, covers the viewer and offers the toggle, but does
not decide what is sensitive: the module that stores the level (photos, on
its MediaItem rows) plugs in from its ``AppConfig.ready()``, so the dependency
points at ``files`` and never back. Without a provider nothing is sensitive
and nothing can be marked.

A file has a sensitivity level, from 0 (nothing to hide) to ``MAX_LEVEL``; a
viewer has a ``BlurPreference``. The preference blurs every file whose level
is at least its rank (``blur_from``), so a stricter preference blurs more.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db import models
from django.db.models import IntegerField, Value
from django.utils.translation import gettext_lazy as _

from workspace.core.setting_keys import MODULE as CORE_MODULE
from workspace.core.setting_keys import SENSITIVE_BLUR
from workspace.users.services.settings import get_module_settings

from .models import File


class BlurPreference(models.TextChoices):
    """How much of what is sensitive a viewer blurs, strictest first."""

    STRICT = "strict", _("Strict")
    HIGH = "high", _("High")
    STANDARD = "standard", _("Standard")
    LOW = "low", _("Low")
    OFF = "off", _("Off")


DEFAULT_BLUR = BlurPreference.STANDARD

# The level of a file marked sensitive by hand: blurred under every
# preference but OFF.
MAX_LEVEL = len(BlurPreference) - 1


def blur_from(preference):
    """The lowest file level *preference* blurs, None for OFF."""
    if preference == BlurPreference.OFF:
        return None
    return list(BlurPreference).index(preference) + 1


def parse_blur_preference(value):
    """*value*, a stored ``core/sensitive_blur`` setting, as a BlurPreference;
    the default when it is unset or unreadable."""
    if value in BlurPreference.values:
        return BlurPreference(value)
    return DEFAULT_BLUR


def blur_preference(user):
    """*user*'s BlurPreference, the default for a visitor nobody signed in."""
    if not user.is_authenticated:
        return DEFAULT_BLUR
    return parse_blur_preference(
        get_module_settings(user, CORE_MODULE).get(SENSITIVE_BLUR)
    )


def is_blurred(level, preference):
    """Whether a file of sensitivity *level* is blurred under *preference*."""
    threshold = blur_from(preference)
    return bool(level) and threshold is not None and level >= threshold


@dataclass(frozen=True)
class SensitivityProvider:
    # An expression over a File row (OuterRef("pk")): the file's level, from
    # 0 to MAX_LEVEL, for a file that can be marked, null for one that cannot.
    level: Callable
    # Marks the files of the given uuids sensitive (True) or not (False);
    # returns how many it marked.
    mark: Callable


_provider = None


def register_sensitivity_provider(provider):
    global _provider
    _provider = provider


def with_sensitivity(files_qs):
    """*files_qs* annotated with ``sensitive_level``: from 0 to MAX_LEVEL, or
    None for a file that cannot be marked."""
    if _provider is None:
        return files_qs.annotate(
            sensitive_level=Value(None, output_field=IntegerField(null=True))
        )
    return files_qs.annotate(sensitive_level=_provider.level())


def sensitive_level(file_obj):
    """*file_obj*'s sensitivity level; None when the file cannot be marked."""
    if hasattr(file_obj, "sensitive_level"):
        return file_obj.sensitive_level
    return (
        with_sensitivity(File.objects.filter(pk=file_obj.pk))
        .values_list("sensitive_level", flat=True)
        .first()
    )


def mark_sensitive(file_uuids, sensitive):
    """Mark the files *file_uuids* sensitive or not; returns how many."""
    if _provider is None:
        return 0
    return _provider.mark(list(file_uuids), sensitive)
