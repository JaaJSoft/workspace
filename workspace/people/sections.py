"""Sections other modules contribute to a person's detail page.

``people`` owns the registry; a contributor registers from its own
``AppConfig.ready()``, so the dependency points at ``people`` and never back.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def _always(user, person):
    return True


@dataclass(frozen=True)
class PersonSection:
    slug: str
    label: str
    icon: str
    template: str
    order: int = 0
    is_visible: Callable = field(default=_always)


class SectionRegistry:
    def __init__(self):
        self._sections = {}

    def register(self, section):
        if section.slug in self._sections:
            raise ValueError(f"Person section '{section.slug}' is already registered.")
        self._sections[section.slug] = section

    def unregister(self, slug):
        self._sections.pop(slug, None)

    def all(self):
        return sorted(self._sections.values(), key=lambda s: (s.order, s.slug))

    def for_person(self, user, person):
        """Visible sections in order. A rule that raises hides its section:
        a broken contributor must not take the whole page down."""
        visible = []
        for section in self.all():
            try:
                if section.is_visible(user, person):
                    visible.append(section)
            except Exception:
                logger.exception(
                    "Person section '%s' visibility failed", scrub(section.slug)
                )
        return visible


section_registry = SectionRegistry()


def render_sections(request, user, person):
    """``[(section, html)]`` for the panel. Rendering happens here rather
    than through ``{% include %}`` so a failing template is skipped."""
    rendered = []
    for section in section_registry.for_person(user, person):
        try:
            html = render_to_string(
                section.template, {"person": person, "user": user}, request=request
            )
        except Exception:
            logger.exception(
                "Person section '%s' failed to render", scrub(section.slug)
            )
            continue
        rendered.append((section, mark_safe(html)))
    return rendered
