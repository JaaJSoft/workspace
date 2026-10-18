"""The scripts and stylesheets the registered properties sections need, for
the pages that host the panel (see files/properties_sections.py)."""

from django import template
from django.templatetags.static import static
from django.utils.html import format_html_join

from workspace.files.properties_sections import properties_section_registry

register = template.Library()


def _paths(attribute):
    paths = (
        path
        for section in properties_section_registry.all()
        for path in getattr(section, attribute)
    )
    return [(static(path),) for path in dict.fromkeys(paths)]


@register.simple_tag
def properties_section_stylesheets():
    return format_html_join(
        "\n", '<link rel="stylesheet" href="{}" />', _paths("stylesheets")
    )


@register.simple_tag
def properties_section_scripts():
    return format_html_join("\n", '<script src="{}"></script>', _paths("scripts"))
