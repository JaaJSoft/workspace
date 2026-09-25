from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.test.utils import override_settings
from django.urls import reverse

from workspace.files.properties_sections import (
    PropertiesSection,
    properties_section_registry,
    render_properties_sections,
)
from workspace.files.services import FileService

User = get_user_model()

TEST_TEMPLATES = Path(__file__).parent / "templates"
TEST_SLUGS = ("fake", "hidden", "broken", "second", "greeting")


@override_settings(
    TEMPLATES=[
        {
            **settings.TEMPLATES[0],
            "DIRS": [*settings.TEMPLATES[0]["DIRS"], TEST_TEMPLATES],
        }
    ]
)
class PropertiesSectionRegistryTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.file = FileService.create_file(
            self.alice, "doc.txt", acting_user=self.alice
        )
        self.request = RequestFactory().get("/files")
        self.request.user = self.alice

    def tearDown(self):
        for slug in TEST_SLUGS:
            properties_section_registry.unregister(slug)

    def _section(self, slug, **kwargs):
        return PropertiesSection(
            slug=slug,
            label=slug.title(),
            template=kwargs.pop("template", "files/tests/blank_section.html"),
            **kwargs,
        )

    def _slugs(self):
        return [
            s.slug
            for s in properties_section_registry.for_file(self.alice, self.file)
            if s.slug in TEST_SLUGS
        ]

    def test_register_and_order(self):
        properties_section_registry.register(self._section("second", order=20))
        properties_section_registry.register(self._section("fake", order=10))

        self.assertEqual(self._slugs(), ["fake", "second"])

    def test_duplicate_slug_rejected(self):
        properties_section_registry.register(self._section("fake"))

        with self.assertRaises(ValueError):
            properties_section_registry.register(self._section("fake"))

    def test_is_visible_filters(self):
        properties_section_registry.register(
            self._section("hidden", is_visible=lambda user, file_obj: False)
        )

        self.assertEqual(self._slugs(), [])

    def test_raising_visibility_is_skipped(self):
        def boom(user, file_obj):
            raise RuntimeError("nope")

        properties_section_registry.register(self._section("broken", is_visible=boom))

        with self.assertLogs("workspace.files.properties_sections", level="ERROR"):
            self.assertEqual(self._slugs(), [])

    def test_render_passes_the_file_and_the_contributed_context(self):
        properties_section_registry.register(
            self._section(
                "greeting",
                template="files/tests/hello_section.html",
                get_context=lambda user, file_obj: {"greeting": user.username},
            )
        )

        rendered = dict(
            (s.slug, html)
            for s, html in render_properties_sections(
                self.request, self.alice, self.file
            )
        )

        self.assertIn("Hello doc.txt from alice", rendered["greeting"])

    def test_render_skips_a_broken_contributor(self):
        def boom(user, file_obj):
            raise RuntimeError("nope")

        properties_section_registry.register(
            self._section("broken", template="files/tests/missing.html")
        )
        properties_section_registry.register(self._section("second", get_context=boom))
        properties_section_registry.register(
            self._section("fake", template="files/tests/hello_section.html")
        )

        with self.assertLogs("workspace.files.properties_sections", level="ERROR"):
            rendered = render_properties_sections(self.request, self.alice, self.file)

        slugs = [s.slug for s, _ in rendered if s.slug in TEST_SLUGS]
        self.assertEqual(slugs, ["fake"])

    def test_the_properties_panel_renders_the_sections(self):
        properties_section_registry.register(
            self._section(
                "greeting",
                template="files/tests/hello_section.html",
                get_context=lambda user, file_obj: {"greeting": "the registry"},
            )
        )
        self.client.force_login(self.alice)

        response = self.client.get(
            reverse("files_ui:properties", kwargs={"uuid": self.file.uuid})
        )

        self.assertContains(response, 'data-properties-section="greeting"')
        self.assertContains(response, "Hello doc.txt from the registry")
        self.assertContains(response, "Greeting</div>")
