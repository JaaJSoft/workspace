from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.dashboard.views import _build_dashboard_context

User = get_user_model()


class DashboardContextTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='dashuser', email='dash@test.com', password='pass123',
        )

    @patch('workspace.dashboard.views.registry')
    def test_context_includes_pending_action_counts_on_modules(self, mock_registry):
        mock_registry.get_for_template.return_value = [
            {'slug': 'chat', 'name': 'Chat', 'active': True},
            {'slug': 'calendar', 'name': 'Calendar', 'active': True},
            {'slug': 'dashboard', 'name': 'Dashboard', 'active': True},
        ]
        mock_registry.get_pending_action_counts.return_value = {'chat': 5, 'calendar': 2}

        context = _build_dashboard_context(self.user)

        modules = context['modules']
        self.assertEqual(len(modules), 2)  # dashboard excluded
        chat_mod = next(m for m in modules if m['slug'] == 'chat')
        cal_mod = next(m for m in modules if m['slug'] == 'calendar')
        self.assertEqual(chat_mod['pending_action_count'], 5)
        self.assertEqual(cal_mod['pending_action_count'], 2)

    @patch('workspace.dashboard.views.registry')
    def test_context_defaults_pending_action_count_to_zero(self, mock_registry):
        mock_registry.get_for_template.return_value = [
            {'slug': 'files', 'name': 'Files', 'active': True},
        ]
        mock_registry.get_pending_action_counts.return_value = {}

        context = _build_dashboard_context(self.user)

        files_mod = context['modules'][0]
        self.assertEqual(files_mod['pending_action_count'], 0)
