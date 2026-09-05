"""The registry machinery every module's action registry is built on.

Exercised with throwaway registries and actions: the production action sets
have their own tests in their modules, and a machinery test that leaned on
one of them would fail whenever that action's rules change.
"""

from enum import StrEnum

from django.test import SimpleTestCase

from workspace.common.actions import BaseAction, BaseActionRegistry


class _Category(StrEnum):
    EDIT = "edit"
    DANGER = "danger"


class _Target:
    kind = "file"


class _Registry(BaseActionRegistry):
    """A registry whose only rule is the target's kind."""

    @classmethod
    def applies_to(cls, action, obj):
        return obj.kind in action.kinds


def _action(**attrs):
    namespace = {
        "id": "sample",
        "label": "Sample",
        "icon": "circle",
        "category": _Category.EDIT,
        "kinds": ("file",),
        "is_available": lambda self, user, obj, **state: state.get("allowed", True),
    }
    namespace.update(attrs)
    return type("SampleAction", (BaseAction,), namespace)


class RegistryIsolationTests(SimpleTestCase):
    def test_each_subclass_holds_its_own_actions(self):
        """Without a fresh list per subclass, a registry built for a test
        would append into the base list and every other registry would see
        its actions for the rest of the process."""
        first = type("_First", (_Registry,), {})
        second = type("_Second", (_Registry,), {})
        first.register(_action(id="only-in-first"))
        self.assertEqual([action.id for action in first.all()], ["only-in-first"])
        self.assertEqual(second.all(), [])
        self.assertIsNone(second.get("only-in-first"))

    def test_all_returns_a_copy(self):
        registry = type("_Copy", (_Registry,), {})
        registry.register(_action())
        registry.all().clear()
        self.assertEqual(len(registry.all()), 1)


class RegistrationTests(SimpleTestCase):
    def setUp(self):
        self.registry = type("_TestRegistry", (_Registry,), {})

    def test_register_returns_the_class_so_it_works_as_a_decorator(self):
        action_cls = _action()
        self.assertIs(self.registry.register(action_cls), action_cls)

    def test_registration_order_is_the_order_actions_are_offered_in(self):
        """Menus are grouped by category and the modules rely on their
        import order to keep each category contiguous."""
        for action_id in ("first", "second", "third"):
            self.registry.register(_action(id=action_id))
        self.assertEqual(
            [
                action["id"]
                for action in self.registry.get_available_actions(None, _Target())
            ],
            ["first", "second", "third"],
        )

    def test_get_answers_by_id_and_none_for_the_unknown(self):
        self.registry.register(_action(id="known"))
        self.assertEqual(self.registry.get("known").id, "known")
        self.assertIsNone(self.registry.get("ghost"))


class AvailabilityTests(SimpleTestCase):
    def setUp(self):
        self.registry = type("_TestRegistry", (_Registry,), {})

    def test_state_reaches_is_available_as_keywords(self):
        self.registry.register(_action())
        self.assertEqual(
            len(self.registry.get_available_actions(None, _Target(), allowed=True)), 1
        )
        self.assertEqual(
            self.registry.get_available_actions(None, _Target(), allowed=False), []
        )

    def test_a_target_the_action_does_not_apply_to_is_never_asked(self):
        """applies_to is the structural gate: an action that is not for this
        kind of target must not have its rules consulted at all, because
        those rules may read attributes only the right kind carries."""
        asked = []

        def is_available(self, user, obj, **state):
            asked.append(obj)
            return True

        self.registry.register(_action(kinds=("folder",), is_available=is_available))
        self.assertEqual(self.registry.get_available_actions(None, _Target()), [])
        self.assertFalse(self.registry.is_action_available("sample", None, _Target()))
        self.assertEqual(asked, [])

    def test_is_action_available_answers_for_one_id(self):
        self.registry.register(_action())
        self.assertTrue(
            self.registry.is_action_available("sample", None, _Target(), allowed=True)
        )
        self.assertFalse(
            self.registry.is_action_available("sample", None, _Target(), allowed=False)
        )

    def test_is_action_available_is_false_for_an_id_nobody_registered(self):
        self.assertFalse(self.registry.is_action_available("ghost", None, _Target()))

    def test_the_base_registry_applies_every_action_to_every_target(self):
        registry = type("_Plain", (BaseActionRegistry,), {})
        registry.register(_action(kinds=()))
        self.assertEqual(len(registry.get_available_actions(None, _Target())), 1)


class SerializationTests(SimpleTestCase):
    def setUp(self):
        self.registry = type("_TestRegistry", (_Registry,), {})

    def _serialized(self):
        return self.registry.get_available_actions(None, _Target())[0]

    def test_it_carries_exactly_what_a_menu_needs(self):
        self.registry.register(_action(css_class="text-error", supports_bulk=True))
        self.assertEqual(
            self._serialized(),
            {
                "id": "sample",
                "label": "Sample",
                "icon": "circle",
                "category": "edit",
                "css_class": "text-error",
                "bulk": True,
            },
        )

    def test_the_category_serialises_as_its_value(self):
        self.registry.register(_action(category=_Category.DANGER))
        self.assertEqual(self._serialized()["category"], "danger")

    def test_label_icon_and_css_class_may_depend_on_the_target(self):
        """A toggle reads as 'Add' or 'Remove' depending on the row, so the
        three presentational fields go through overridable hooks."""
        self.registry.register(
            _action(
                get_label=lambda self, obj: f"Label for {obj.kind}",
                get_icon=lambda self, obj: f"icon-{obj.kind}",
                get_css_class=lambda self, obj: f"css-{obj.kind}",
            )
        )
        data = self._serialized()
        self.assertEqual(data["label"], "Label for file")
        self.assertEqual(data["icon"], "icon-file")
        self.assertEqual(data["css_class"], "css-file")
