from django.template import Context, Template
from django.test import SimpleTestCase


def render(breadcrumbs, **extra):
    template = Template(
        '{% include "ui/partials/breadcrumbs.html" with breadcrumbs=breadcrumbs %}'
    )
    return template.render(Context({"breadcrumbs": breadcrumbs, **extra}))


class BreadcrumbDataAttributesTests(SimpleTestCase):
    """A crumb's ``data`` dict lands on its link as ``data-*`` attributes,
    which is how a module hooks behaviour (a drop target, say) onto the
    trail without the partial knowing about it."""

    def test_data_renders_as_data_attributes_on_the_link(self):
        html = render(
            [
                {"label": "Root", "url": "/x", "data": {"hook": "", "name": "R"}},
                {"label": "Leaf", "url": "/x/leaf", "data": {"hook": "leaf"}},
            ]
        )
        self.assertIn('href="/x" data-hook="" data-name="R"', html)
        self.assertIn('href="/x/leaf" data-hook="leaf"', html)

    def test_data_values_are_escaped(self):
        html = render([{"label": "Root", "url": "/x", "data": {"name": 'a"b<c'}}])
        self.assertIn('data-name="a&quot;b&lt;c"', html)

    def test_a_crumb_without_data_renders_as_before(self):
        html = render([{"label": "Root", "url": "/x"}, {"label": "Here"}])
        self.assertIn('<a href="/x" class=', html)
        self.assertNotIn("data-", html.split("<nav")[1].split("<i")[0])

    def test_collapsed_trail_keeps_the_hooks_on_every_link(self):
        crumbs = [
            {"label": f"L{i}", "url": f"/l{i}", "data": {"hook": f"l{i}"}}
            for i in range(6)
        ]
        html = render(crumbs, collapse=True, collapse_after=3)
        for i in range(6):
            self.assertIn(f'href="/l{i}" data-hook="l{i}"', html, i)
