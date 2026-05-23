from django.contrib.auth import get_user_model
from django.test import TestCase
from pydantic import ValidationError

from workspace.chat.ai_tools import (
    AskUserQuestionParams, ChatToolProvider,
)

User = get_user_model()


class AskUserQuestionToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='a@test.com', password='pw',
        )
        self.bot = User.objects.create_user(
            username='bot', email='b@test.com', password='pw',
        )
        self.provider = ChatToolProvider()

    def _run(self, question='Pick one', options=None, context=None):
        opts = options if options is not None else ['Yes', 'No']
        args = AskUserQuestionParams(question=question, options=opts)
        ctx = context if context is not None else {}
        result = self.provider.ask_user_question(
            args, user=self.user, bot=self.bot,
            conversation_id=None, context=ctx,
        )
        return result, ctx

    def test_nominal_writes_context_and_sets_stop_flag(self):
        result, ctx = self._run('Tone?', ['Formal', 'Casual'])
        self.assertNotIn('Error', result)
        self.assertEqual(ctx['question']['question'], 'Tone?')
        self.assertEqual(ctx['question']['options'], ['Formal', 'Casual'])
        self.assertTrue(ctx['stop_after_round'])

    def test_dedupes_and_trims_options(self):
        result, ctx = self._run('Q', ['  Yes ', 'No', ' Yes ', 'no'])
        self.assertEqual(ctx['question']['options'], ['Yes', 'No', 'no'])

    def test_caps_options_at_six(self):
        opts = ['A', 'B', 'C', 'D', 'E', 'F']
        result, ctx = self._run('Q', opts)
        self.assertEqual(len(ctx['question']['options']), 6)

    def test_setdefault_keeps_first_question(self):
        ctx = {}
        self._run('First?', ['A', 'B'], context=ctx)
        self._run('Second?', ['X', 'Y'], context=ctx)
        self.assertEqual(ctx['question']['question'], 'First?')

    def test_fewer_than_two_options_returns_error(self):
        args = AskUserQuestionParams(question='Q', options=['', '   '])
        ctx = {}
        result = self.provider.ask_user_question(
            args, user=self.user, bot=self.bot,
            conversation_id=None, context=ctx,
        )
        self.assertIn('Error', result)
        self.assertNotIn('question', ctx)
        self.assertNotIn('stop_after_round', ctx)

    def test_pydantic_rejects_one_option(self):
        with self.assertRaises(ValidationError):
            AskUserQuestionParams(question='Q', options=['Only one'])

    def test_pydantic_rejects_seven_options(self):
        with self.assertRaises(ValidationError):
            AskUserQuestionParams(
                question='Q', options=['1', '2', '3', '4', '5', '6', '7'],
            )

    def test_pydantic_rejects_empty_question(self):
        with self.assertRaises(ValidationError):
            AskUserQuestionParams(question='', options=['A', 'B'])
