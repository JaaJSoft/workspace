from dataclasses import replace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.core.module_registry import registry
from workspace.core.today_registry import TodayColumn, TodayRegistry, TodaySource

User = get_user_model()


def _source(slug, module_slug="chat", order=100):
    return TodaySource(
        slug=slug,
        module_slug=module_slug,
        label=slug,
        description="",
        icon="circle",
        url="/",
        column=TodayColumn.TODO,
        items_fn=lambda user, now, tz: [],
        template="",
        order=order,
    )


class TodayRegistryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="viewer", password="pw")
        self.today = TodayRegistry()

    def test_a_slug_registers_once(self):
        self.today.register(_source("inbox"))

        with self.assertRaises(ValueError):
            self.today.register(_source("inbox"))

    def test_sources_come_in_order_then_slug(self):
        for source in (
            _source("b", order=2),
            _source("c", order=1),
            _source("a", order=2),
        ):
            self.today.register(source)

        slugs = [source.slug for source in self.today.visible_sources(self.user)]

        self.assertEqual(slugs, ["c", "a", "b"])

    @override_settings(PREVIEW_VISIBILITY="none")
    def test_a_source_follows_its_modules_visibility(self):
        self.today.register(_source("inbox", module_slug="mail"))
        self.today.register(_source("talk", module_slug="chat"))

        with patch.dict(
            registry._modules,
            {"mail": replace(registry.get("mail"), preview=True)},
        ):
            slugs = [source.slug for source in self.today.visible_sources(self.user)]

        self.assertEqual(slugs, ["talk"])

    def test_every_module_source_is_registered(self):
        from workspace.core.today_registry import today_registry

        with override_settings(PREVIEW_VISIBILITY="all"):
            slugs = {
                source.slug for source in today_registry.visible_sources(self.user)
            }

        self.assertEqual(slugs, {"events", "invitations", "tasks", "mail"})
