"""The OpenAPI schema and its documentation pages are API routes like any other."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class ApiDocsRoutesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="reader", password="pass")

    def setUp(self):
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_routes_live_under_api_without_trailing_slash(self):
        self.assertEqual(
            [reverse(name) for name in ("schema", "swagger-ui", "redoc")],
            ["/api/schema", "/api/schema/swagger-ui", "/api/schema/redoc"],
        )

    def test_documentation_pages_load_the_schema_route(self):
        for name in ("swagger-ui", "redoc"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "/api/schema")

    def test_schema_is_served(self):
        response = self.client.get(reverse("schema"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"openapi", response.content)

    def test_pages_require_a_login(self):
        self.client.logout()
        for name in ("schema", "swagger-ui", "redoc"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)
