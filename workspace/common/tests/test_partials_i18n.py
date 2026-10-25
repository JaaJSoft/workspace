from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils import translation


def render_in_french(template, context=None):
    with translation.override("fr"):
        return render_to_string(template, context or {})


class UiKitFrenchTests(SimpleTestCase):
    def test_dialogs_render_in_the_active_language(self):
        html = render_in_french("ui/partials/dialogs.html")
        self.assertIn("Êtes-vous sûr", html)
        self.assertIn("Annuler", html)
        self.assertNotIn("Are you sure?", html)

    def test_default_labels_are_translated(self):
        html = render_in_french(
            "ui/partials/refresh_button.html", {"url_expr": "url", "target": "t"}
        )
        self.assertIn('title="Actualiser"', html)

    def test_a_caller_label_still_wins_over_the_default(self):
        html = render_in_french(
            "ui/partials/refresh_button.html",
            {"url_expr": "url", "target": "t", "title": "Reload feed"},
        )
        self.assertIn('title="Reload feed"', html)

    def test_chip_label_hands_the_browser_a_whole_sentence(self):
        # The name lands inside the translated sentence in the browser, so the
        # placeholder must reach it intact rather than escaped.
        html = render_in_french(
            "ui/partials/user_chip.html",
            {
                "user_id_expr": "u.id",
                "username_expr": "u.username",
                "remove_expr": "remove(u.id)",
            },
        )
        self.assertIn(
            "interpolate('Retirer %(name)s', { name: u.username }, true)", html
        )
