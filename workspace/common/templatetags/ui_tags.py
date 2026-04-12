import re

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_ITEM_SEP = re.compile(r'^\s*---\s*$', re.MULTILINE)


class HelpItemsNode(template.Node):
    """
    Parse a block of help item definitions into a list of dicts stored in the
    template context under ``var_name``.

    Usage::

        {% help_items as items %}
        icon-name | Item Title [| checked]
        <p>Arbitrary HTML content…</p>
        ---
        another-icon | Another Title
        <ul>…</ul>
        {% endhelp_items %}

    Each item block starts with a header line ``icon | title [| checked]``
    followed by HTML content. Blocks are separated by a line containing only
    ``---``. The rendered list can then be passed to the help_dialog partial::

        {% include "ui/partials/help_dialog.html" with dialog_id="…" accent_color="…" items=items %}
    """

    def __init__(self, nodelist, var_name):
        self.nodelist = nodelist
        self.var_name = var_name

    def render(self, context):
        rendered = self.nodelist.render(context)
        items = []
        for block in _ITEM_SEP.split(rendered):
            lines = block.strip().splitlines()
            # Find first non-empty line as the item header
            header_line = None
            header_idx = 0
            for i, line in enumerate(lines):
                if line.strip():
                    header_line = line
                    header_idx = i
                    break
            if header_line is None:
                continue
            parts = [p.strip() for p in header_line.split('|')]
            icon = parts[0]
            title = parts[1] if len(parts) > 1 else ''
            checked = len(parts) > 2 and parts[2].lower() in ('checked', 'true', '1', 'yes')
            content = mark_safe('\n'.join(lines[header_idx + 1:]).strip())
            items.append({'icon': icon, 'title': title, 'checked': checked, 'content': content})
        context[self.var_name] = items
        return ''


@register.tag('help_items')
def do_help_items(parser, token):
    bits = token.split_contents()
    if len(bits) != 3 or bits[1] != 'as':
        raise template.TemplateSyntaxError(
            "'help_items' tag requires: {% help_items as variable_name %}"
        )
    var_name = bits[2]
    nodelist = parser.parse(('endhelp_items',))
    parser.delete_first_token()
    return HelpItemsNode(nodelist, var_name)
