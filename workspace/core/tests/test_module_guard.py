import re
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

User = get_user_model()


# The page masks its CSRF token afresh on every render, and the ETag is a hash
# of the body: two renders of the very same page differ in those two values.
_CSRF_VALUE = re.compile(rb'(name="csrfmiddlewaretoken" value=")[^"]+"')


def _snapshot(response):
    """Everything a caller could compare between two answers."""
    body = _CSRF_VALUE.sub(rb'\1"', response.content)
    headers = sorted(
        (name, value) for name, value in response.headers.items() if name != "ETag"
    )
    return response.status_code, body, headers


@override_settings(PREVIEW_VISIBILITY="staff")
class PageGuardTests(TestCase):
    def setUp(self):
        self.regular = User.objects.create_user(username="regular", password="pw")
        self.staff = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )

    def tearDown(self):
        cache.clear()

    def test_a_hidden_page_answers_exactly_like_an_absent_one(self):
        self.client.force_login(self.regular)
        hidden = self.client.get("/vault")
        absent = self.client.get("/vault/does-not-exist")
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(_snapshot(hidden), _snapshot(absent))

    def test_every_page_of_a_preview_module_is_refused(self):
        self.client.force_login(self.regular)
        for path in (
            "/vault",
            "/vault/onboarding",
            f"/vault/{uuid.uuid4()}",
            "/imports",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_the_audience_still_reaches_the_page(self):
        self.client.force_login(self.staff)
        self.assertNotEqual(self.client.get("/vault").status_code, 404)

    def test_an_anonymous_visitor_is_still_sent_to_the_login_page(self):
        response = self.client.get("/vault")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    @override_settings(PREVIEW_VISIBILITY="none")
    def test_a_module_out_of_preview_is_never_refused(self):
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get("/files").status_code, 200)
