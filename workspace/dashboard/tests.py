from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory

from workspace.dashboard.views import (
    _build_dashboard_context,
    _get_activity_context,
    _get_upcoming_events,
    ACTIVITY_LIMIT,
)

User = get_user_model()


# ── _build_dashboard_context ────────────────────────────────────

class BuildDashboardContextTests(TestCase):

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

    @patch('workspace.dashboard.views.registry')
    def test_excludes_dashboard_from_modules(self, mock_registry):
        mock_registry.get_for_template.return_value = [
            {'slug': 'dashboard', 'name': 'Dashboard', 'active': True},
            {'slug': 'mail', 'name': 'Mail', 'active': True},
        ]
        mock_registry.get_pending_action_counts.return_value = {}

        context = _build_dashboard_context(self.user)
        slugs = [m['slug'] for m in context['modules']]
        self.assertNotIn('dashboard', slugs)
        self.assertIn('mail', slugs)

    @patch('workspace.dashboard.views.registry')
    def test_context_includes_upcoming_events(self, mock_registry):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_pending_action_counts.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertIn('upcoming_events', context)

    @patch('workspace.dashboard.views.registry')
    def test_context_includes_usage_stats(self, mock_registry):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_pending_action_counts.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertIn('usage_stats', context)

    @patch('workspace.dashboard.views.registry')
    def test_context_includes_storage_quota(self, mock_registry):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_pending_action_counts.return_value = {}

        context = _build_dashboard_context(self.user)
        self.assertIn('storage_quota', context)

    @patch('workspace.dashboard.views._get_activity_context')
    @patch('workspace.dashboard.views.registry')
    def test_includes_activity_when_requested(self, mock_registry, mock_activity):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_pending_action_counts.return_value = {}
        mock_activity.return_value = {'activity_events': []}

        context = _build_dashboard_context(self.user, include_activity=True)
        mock_activity.assert_called_once()
        self.assertIn('activity_events', context)

    @patch('workspace.dashboard.views._get_activity_context')
    @patch('workspace.dashboard.views.registry')
    def test_excludes_activity_when_not_requested(self, mock_registry, mock_activity):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_pending_action_counts.return_value = {}

        context = _build_dashboard_context(self.user, include_activity=False)
        mock_activity.assert_not_called()
        self.assertNotIn('activity_events', context)

    @patch('workspace.dashboard.views._get_activity_context')
    @patch('workspace.dashboard.views.registry')
    def test_forwards_activity_source(self, mock_registry, mock_activity):
        mock_registry.get_for_template.return_value = []
        mock_registry.get_pending_action_counts.return_value = {}
        mock_activity.return_value = {}

        _build_dashboard_context(self.user, activity_source='chat')
        mock_activity.assert_called_once_with(self.user, source='chat')


# ── _get_activity_context ───────────────────────────────────────

class GetActivityContextTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='actuser', password='pass123',
        )

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_returns_expected_keys(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = [{'slug': 'chat', 'label': 'Chat'}]

        ctx = _get_activity_context(self.user)

        expected_keys = {
            'activity_events', 'activity_sources', 'activity_source',
            'activity_search', 'activity_has_more', 'activity_next_offset',
            'activity_prefix', 'activity_base_url',
        }
        self.assertEqual(set(ctx.keys()), expected_keys)

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_has_more_when_extra_events_returned(self, mock_events, mock_annotate, mock_sources):
        # Return ACTIVITY_LIMIT+1 events to trigger has_more
        mock_events.return_value = [{'id': i} for i in range(ACTIVITY_LIMIT + 1)]
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user)

        self.assertTrue(ctx['activity_has_more'])
        self.assertEqual(len(ctx['activity_events']), ACTIVITY_LIMIT)

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_no_more_when_fewer_events(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = [{'id': 1}]
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user)

        self.assertFalse(ctx['activity_has_more'])
        self.assertEqual(len(ctx['activity_events']), 1)

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_offset_forwarded_to_next_offset(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user, offset=20)

        self.assertEqual(ctx['activity_next_offset'], 20 + ACTIVITY_LIMIT)

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_source_filter_passed_to_service(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = []

        _get_activity_context(self.user, source='files')

        call_kwargs = mock_events.call_args.kwargs
        self.assertEqual(call_kwargs['source'], 'files')

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_search_filter_passed_to_service(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = []

        _get_activity_context(self.user, search='deploy')

        call_kwargs = mock_events.call_args.kwargs
        self.assertEqual(call_kwargs['search'], 'deploy')

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_empty_search_becomes_none(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = []

        ctx = _get_activity_context(self.user, search=None)

        self.assertEqual(ctx['activity_search'], '')

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_annotate_time_ago_called(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = [{'id': 1}]
        mock_sources.return_value = []

        _get_activity_context(self.user)

        mock_annotate.assert_called_once()

    @patch('workspace.dashboard.views.get_sources')
    @patch('workspace.dashboard.views.annotate_time_ago')
    @patch('workspace.dashboard.views.get_recent_events')
    def test_excludes_current_user_events(self, mock_events, mock_annotate, mock_sources):
        mock_events.return_value = []
        mock_sources.return_value = []

        _get_activity_context(self.user)

        call_kwargs = mock_events.call_args.kwargs
        self.assertEqual(call_kwargs['exclude_user_id'], self.user.id)


# ── _get_upcoming_events ────────────────────────────────────────

class GetUpcomingEventsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='eventuser', password='pass123',
        )

    @patch('workspace.dashboard.views.get_upcoming_for_user')
    def test_calls_upstream_with_correct_range(self, mock_upcoming):
        mock_upcoming.return_value = []

        result = _get_upcoming_events(self.user)

        self.assertEqual(result, [])
        mock_upcoming.assert_called_once()
        args = mock_upcoming.call_args
        self.assertEqual(args[0][0], self.user)
        now_arg = args[0][1]
        end_arg = args[0][2]
        # End of day should be after now
        self.assertGreaterEqual(end_arg, now_arg)
        # Same date
        self.assertEqual(now_arg.date(), end_arg.date())


# ── index view ──────────────────────────────────────────────────

class IndexViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='viewuser', password='pass123',
        )

    def test_requires_login(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_returns_200_for_authenticated_user(self, mock_ctx):
        mock_ctx.return_value = {'modules': [], 'upcoming_events': [], 'usage_stats': {}}
        self.client.login(username='viewuser', password='pass123')
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)

    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_sets_activity_tab_to_all(self, mock_ctx):
        mock_ctx.return_value = {'modules': []}
        self.client.login(username='viewuser', password='pass123')
        resp = self.client.get('/')
        self.assertEqual(resp.context['activity_tab'], 'all')


# ── activity_feed view ──────────────────────────────────────────

class ActivityFeedViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='feeduser', password='pass123',
        )
        self.client.login(username='feeduser', password='pass123')

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get('/dashboard/activity')
        self.assertEqual(resp.status_code, 302)

    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_full_page_without_alpine_header(self, mock_ctx):
        mock_ctx.return_value = {'modules': []}
        resp = self.client.get('/dashboard/activity')
        self.assertEqual(resp.status_code, 200)
        # Full page renders the dashboard template
        self.assertTemplateUsed(resp, 'dashboard/index.html')

    @patch('workspace.dashboard.views._get_activity_context')
    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_alpine_request_returns_partial(self, mock_ctx, mock_activity):
        mock_ctx.return_value = {'modules': []}
        mock_activity.return_value = {
            'activity_events': [], 'activity_sources': [],
            'activity_source': None, 'activity_search': '',
            'activity_has_more': False, 'activity_next_offset': 10,
            'activity_prefix': 'dashboard-activity',
            'activity_base_url': '/dashboard/activity',
        }
        resp = self.client.get(
            '/dashboard/activity',
            HTTP_X_ALPINE_REQUEST='true',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'ui/partials/activity_feed.html')

    @patch('workspace.dashboard.views._get_activity_context')
    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_alpine_append_uses_activity_page_template(self, mock_ctx, mock_activity):
        mock_ctx.return_value = {'modules': []}
        mock_activity.return_value = {
            'activity_events': [], 'activity_sources': [],
            'activity_source': None, 'activity_search': '',
            'activity_has_more': False, 'activity_next_offset': 20,
            'activity_prefix': 'dashboard-activity',
            'activity_base_url': '/dashboard/activity',
        }
        resp = self.client.get(
            '/dashboard/activity?offset=10',
            HTTP_X_ALPINE_REQUEST='true',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'ui/partials/activity_page.html')

    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_source_param_sets_activity_tab(self, mock_ctx):
        mock_ctx.return_value = {'modules': []}
        resp = self.client.get('/dashboard/activity?source=chat')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['activity_tab'], 'chat')

    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_no_source_defaults_tab_to_all(self, mock_ctx):
        mock_ctx.return_value = {'modules': []}
        resp = self.client.get('/dashboard/activity')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['activity_tab'], 'all')

    @patch('workspace.dashboard.views._get_activity_context')
    @patch('workspace.dashboard.views._build_dashboard_context')
    def test_search_param_forwarded(self, mock_ctx, mock_activity):
        mock_ctx.return_value = {'modules': []}
        mock_activity.return_value = {
            'activity_events': [], 'activity_sources': [],
            'activity_source': None, 'activity_search': 'deploy',
            'activity_has_more': False, 'activity_next_offset': 10,
            'activity_prefix': 'dashboard-activity',
            'activity_base_url': '/dashboard/activity',
        }
        resp = self.client.get(
            '/dashboard/activity?q=deploy',
            HTTP_X_ALPINE_REQUEST='true',
        )
        self.assertEqual(resp.status_code, 200)
        mock_activity.assert_called_once()
        call_kwargs = mock_activity.call_args.kwargs
        self.assertEqual(call_kwargs['search'], 'deploy')
