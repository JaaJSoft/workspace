"""Sections other modules contribute to a file's properties panel.

``files`` owns the registry; a contributor registers from its own
``AppConfig.ready()``, so the dependency points at ``files`` and never back.
The panel is the same partial wherever it opens (the Files browser, the
Photos timeline, any module that reuses it), so a section shows up in all of
them.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def _always(user, file_obj):
    return True


def _no_context(user, file_obj):
    return {}


@dataclass(frozen=True)
class PropertiesSection:
    slug: str
    label: str
    template: str
    order: int = 0
    # Called with the viewer and the file. The viewer can already open the
    # file: the panel's own permission check runs first.
    is_visible: Callable = field(default=_always)
    # Extra template context, next to ``file`` and ``user``. Only called for
    # a visible section.
    get_context: Callable = field(default=_no_context)


class PropertiesSectionRegistry:
    def __init__(self):
        self._sections = {}

    def register(self, section):
        if section.slug in self._sections:
            raise ValueError(
                f"Properties section '{section.slug}' is already registered."
            )
        self._sections[section.slug] = section

    def unregister(self, slug):
        self._sections.pop(slug, None)

    def all(self):
        return sorted(self._sections.values(), key=lambda s: (s.order, s.slug))

    def for_file(self, user, file_obj):
        """Visible sections in order. A rule that raises hides its section:
        a broken contributor must not take the whole panel down."""
        visible = []
        for section in self.all():
            try:
                if section.is_visible(user, file_obj):
                    visible.append(section)
            except Exception:
                logger.exception(
                    "Properties section '%s' visibility failed", scrub(section.slug)
                )
        return visible


properties_section_registry = PropertiesSectionRegistry()


def render_properties_sections(request, user, file_obj):
    """``[(section, html)]`` for the panel. Rendering happens here rather
    than through ``{% include %}`` so a failing contributor is skipped."""
    rendered = []
    for section in properties_section_registry.for_file(user, file_obj):
        try:
            context = {
                **section.get_context(user, file_obj),
                "file": file_obj,
                "user": user,
            }
            html = render_to_string(section.template, context, request=request)
        except Exception:
            logger.exception(
                "Properties section '%s' failed to render", scrub(section.slug)
            )
            continue
        rendered.append((section, mark_safe(html)))
    return rendered
