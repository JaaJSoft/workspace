from django.test import SimpleTestCase

from workspace.ai.harness.policies import RepeatGuard

from .harness import call


class RepeatGuardTests(SimpleTestCase):
    def test_allows_a_call_up_to_the_limit_then_refuses(self):
        guard = RepeatGuard(2)

        first = guard.refusal(call("c1", arguments='{"a": 1}'))
        second = guard.refusal(call("c2", arguments='{"a": 1}'))
        third = guard.refusal(call("c3", arguments='{"a": 1}'))

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIn("Not executed", third)
        self.assertIn("search", third)
        self.assertIn("2 times", third)

    def test_reordered_arguments_are_the_same_call(self):
        guard = RepeatGuard(1)

        guard.refusal(call("c1", arguments='{"a": 1, "b": 2}'))

        self.assertIsNotNone(guard.refusal(call("c2", arguments='{"b": 2, "a": 1}')))

    def test_different_arguments_are_a_different_call(self):
        guard = RepeatGuard(1)

        guard.refusal(call("c1", arguments='{"url": "https://a.test"}'))

        self.assertIsNone(
            guard.refusal(call("c2", arguments='{"url": "https://b.test"}'))
        )

    def test_unparseable_arguments_are_compared_as_text(self):
        guard = RepeatGuard(1)

        guard.refusal(call("c1", arguments="not json "))

        self.assertIsNotNone(guard.refusal(call("c2", arguments=" not json")))
