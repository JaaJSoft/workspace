from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.test.utils import override_settings

from workspace.people.sections import PersonSection, render_sections, section_registry
from workspace.people.services.persons import create_person

User = get_user_model()

TEST_TEMPLATES = Path(__file__).parent / "templates"


@override_settings(
    TEMPLATES=[
        {
            **settings.TEMPLATES[0],
            "DIRS": [*settings.TEMPLATES[0]["DIRS"], TEST_TEMPLATES],
        }
    ]
)
class SectionRegistryTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.person = create_person(owner=self.alice, display_name="Bob")
        self.request = RequestFactory().get("/people")
        self.request.user = self.alice

    def tearDown(self):
        for slug in ("fake", "hidden", "broken", "second"):
            section_registry.unregister(slug)

    def test_register_and_order(self):
        section_registry.register(
            PersonSection(
                slug="second",
                label="B",
                icon="x",
                template="people/tests/blank.html",
                order=20,
            )
        )
        section_registry.register(
            PersonSection(
                slug="fake",
                label="A",
                icon="x",
                template="people/tests/blank.html",
                order=10,
            )
        )
        slugs = [s.slug for s in section_registry.for_person(self.alice, self.person)]
        self.assertEqual(slugs, ["fake", "second"])

    def test_duplicate_slug_rejected(self):
        section = PersonSection(
            slug="fake", label="A", icon="x", template="people/tests/blank.html"
        )
        section_registry.register(section)
        with self.assertRaises(ValueError):
            section_registry.register(section)

    def test_is_visible_filters(self):
        section_registry.register(
            PersonSection(
                slug="hidden",
                label="H",
                icon="x",
                template="people/tests/blank.html",
                is_visible=lambda user, person: False,
            )
        )
        self.assertEqual(section_registry.for_person(self.alice, self.person), [])

    def test_raising_visibility_is_skipped(self):
        def boom(user, person):
            raise RuntimeError("nope")

        section_registry.register(
            PersonSection(
                slug="broken",
                label="B",
                icon="x",
                template="people/tests/blank.html",
                is_visible=boom,
            )
        )
        with self.assertLogs("workspace.people.sections", level="ERROR"):
            self.assertEqual(section_registry.for_person(self.alice, self.person), [])

    def test_render_sections_skips_a_broken_template(self):
        section_registry.register(
            PersonSection(
                slug="broken", label="B", icon="x", template="people/tests/missing.html"
            )
        )
        section_registry.register(
            PersonSection(
                slug="fake", label="A", icon="x", template="people/tests/hello.html"
            )
        )
        with self.assertLogs("workspace.people.sections", level="ERROR"):
            rendered = render_sections(self.request, self.alice, self.person)
        self.assertEqual([s.slug for s, _ in rendered], ["fake"])
        self.assertIn("Hello Bob", rendered[0][1])
