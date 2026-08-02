"""Rendering tests for ``ui/partials/inline_alert.html`` actions."""

from django.template.loader import render_to_string
from django.test import TestCase


class InlineAlertActionsTests(TestCase):
    def _render(self, **ctx):
        return render_to_string("ui/partials/inline_alert.html", ctx)

    def test_href_action_renders_a_link(self):
        html = self._render(
            message="m",
            actions=[
                {
                    "label": "Open settings",
                    "href": "/users/settings",
                    "style": "primary",
                }
            ],
        )
        self.assertIn('href="/users/settings"', html)
        self.assertIn("Open settings", html)

    def test_click_action_renders_a_button_with_alpine_handler(self):
        html = self._render(
            message="m", actions=[{"label": "Retry", "click": "retry()"}]
        )
        self.assertIn('@click="retry()"', html)
        self.assertIn("Retry", html)

    def test_dismiss_action_hides_the_alert_via_alpine_state(self):
        html = self._render(message="m", actions=[{"label": "Ignore", "dismiss": True}])
        self.assertIn('@click="show = false"', html)
        self.assertIn("x-data", html)
        self.assertIn("x-show", html)

    def test_click_and_dismiss_chain_both_expressions(self):
        html = self._render(
            message="m", actions=[{"label": "Ok", "click": "ack()", "dismiss": True}]
        )
        self.assertIn('@click="ack(); show = false"', html)

    def test_no_actions_renders_no_action_buttons(self):
        html = self._render(message="m")
        self.assertNotIn("btn-xs", html)
