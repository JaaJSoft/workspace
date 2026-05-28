from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.ai.serializers import AITaskSerializer

User = get_user_model()


class AITaskResultHtmlEscapingTests(TestCase):
    """result_html is rendered into Alpine x-html, so raw HTML in the
    (LLM-produced, attacker-influenced) result must be escaped, not executed."""

    def setUp(self):
        self.user = User.objects.create_user(username='a', password='p')

    def _result_html(self, *, task_type, result, action=None):
        task = AITask.objects.create(
            owner=self.user,
            task_type=task_type,
            status=AITask.Status.COMPLETED,
            result=result,
            input_data={'action': action} if action else {},
        )
        return AITaskSerializer(task).data['result_html']

    def test_summarize_escapes_raw_html(self):
        html = self._result_html(
            task_type=AITask.TaskType.SUMMARIZE,
            result='Hi <img src=x onerror=alert(1)> there',
        )
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img', html)

    def test_editor_explain_escapes_raw_html(self):
        html = self._result_html(
            task_type=AITask.TaskType.EDITOR,
            action='explain',
            result='<script>alert(1)</script>',
        )
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_editor_summarize_escapes_raw_html(self):
        html = self._result_html(
            task_type=AITask.TaskType.EDITOR,
            action='summarize',
            result='Hi <img src=x onerror=alert(1)> there',
        )
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img', html)

    def test_markdown_still_renders(self):
        html = self._result_html(
            task_type=AITask.TaskType.SUMMARIZE,
            result='**bold** text',
        )
        self.assertIn('<strong>bold</strong>', html)
