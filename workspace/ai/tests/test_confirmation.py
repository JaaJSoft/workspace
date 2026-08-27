from django.test import SimpleTestCase

from workspace.ai.services.confirmation import CONFIRM_OPTIONS, request_confirmation


class RequestConfirmationTests(SimpleTestCase):
    def test_asks_the_question_and_halts_the_loop(self):
        context = {}
        result = request_confirmation(context, "Cancel the standup?")

        self.assertEqual(context["question"]["question"], "Cancel the standup?")
        self.assertEqual(context["question"]["options"], CONFIRM_OPTIONS)
        self.assertTrue(context["stop_after_round"])
        self.assertIn("confirm=true", result)

    def test_first_question_of_a_round_wins(self):
        context = {}
        request_confirmation(context, "First?")
        result = request_confirmation(context, "Second?")

        self.assertEqual(context["question"]["question"], "First?")
        self.assertTrue(context["stop_after_round"])
        # The result must name the prompt the user can actually see, or the
        # model treats their answer to "First?" as approval of "Second?".
        self.assertIn("First?", result)
        self.assertNotIn("Second?", result)
