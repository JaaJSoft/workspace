from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.core.module_registry import PendingActionProviderInfo, ModuleInfo, ModuleRegistry, CommandInfo

User = get_user_model()


class PendingActionProviderRegistryTests(TestCase):

    def setUp(self):
        self.registry = ModuleRegistry()
        self.registry.register(ModuleInfo(
            name='Chat', slug='chat', description='Chat module',
            icon='message-circle', color='info', url='/chat', order=10,
        ))
        self.registry.register(ModuleInfo(
            name='Calendar', slug='calendar', description='Calendar module',
            icon='calendar', color='accent', url='/calendar', order=20,
        ))
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )

    def test_register_pending_action_provider(self):
        provider = PendingActionProviderInfo(module_slug='chat', pending_action_fn=lambda u: 5)
        self.registry.register_pending_action_provider(provider)
        counts = self.registry.get_pending_action_counts(self.user)
        self.assertEqual(counts, {'chat': 5})

    def test_register_pending_action_provider_unknown_module_raises(self):
        provider = PendingActionProviderInfo(module_slug='unknown', pending_action_fn=lambda u: 0)
        with self.assertRaises(ValueError):
            self.registry.register_pending_action_provider(provider)

    def test_duplicate_pending_action_provider_raises(self):
        provider = PendingActionProviderInfo(module_slug='chat', pending_action_fn=lambda u: 0)
        self.registry.register_pending_action_provider(provider)
        with self.assertRaises(ValueError):
            self.registry.register_pending_action_provider(provider)

    def test_get_pending_action_counts_multiple_providers(self):
        self.registry.register_pending_action_provider(
            PendingActionProviderInfo(module_slug='chat', pending_action_fn=lambda u: 3),
        )
        self.registry.register_pending_action_provider(
            PendingActionProviderInfo(module_slug='calendar', pending_action_fn=lambda u: 7),
        )
        counts = self.registry.get_pending_action_counts(self.user)
        self.assertEqual(counts, {'chat': 3, 'calendar': 7})

    def test_get_pending_action_counts_empty_when_no_providers(self):
        counts = self.registry.get_pending_action_counts(self.user)
        self.assertEqual(counts, {})

    def test_get_pending_action_counts_handles_provider_exception(self):
        def failing_fn(u):
            raise RuntimeError("oops")

        self.registry.register_pending_action_provider(
            PendingActionProviderInfo(module_slug='chat', pending_action_fn=failing_fn),
        )
        counts = self.registry.get_pending_action_counts(self.user)
        self.assertEqual(counts, {'chat': 0})

    def test_get_pending_action_counts_skips_inactive_modules(self):
        self.registry.register(ModuleInfo(
            name='Notes', slug='notes', description='Notes module',
            icon='notebook-pen', color='accent', url=None, active=False, order=30,
        ))
        self.registry.register_pending_action_provider(
            PendingActionProviderInfo(module_slug='chat', pending_action_fn=lambda u: 3),
        )
        self.registry.register_pending_action_provider(
            PendingActionProviderInfo(module_slug='notes', pending_action_fn=lambda u: 99),
        )
        counts = self.registry.get_pending_action_counts(self.user)
        self.assertEqual(counts, {'chat': 3})


class CommandRegistryTests(TestCase):

    def setUp(self):
        self.registry = ModuleRegistry()
        self.registry.register(ModuleInfo(
            name='Chat', slug='chat', description='Chat module',
            icon='message-circle', color='info', url='/chat', order=10,
        ))
        self.registry.register(ModuleInfo(
            name='Calendar', slug='calendar', description='Calendar module',
            icon='calendar', color='accent', url='/calendar', order=20,
        ))

    def test_register_commands(self):
        cmds = [
            CommandInfo(
                name='Chat', keywords=['chat', 'messages'], icon='message-circle',
                color='info', url='/chat', kind='navigate', module_slug='chat',
            ),
        ]
        self.registry.register_commands(cmds)
        results = self.registry.search_commands('chat')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, 'Chat')

    def test_register_commands_unknown_module_raises(self):
        cmds = [
            CommandInfo(
                name='Notes', keywords=['notes'], icon='notebook', color='accent',
                url='/notes', kind='navigate', module_slug='unknown',
            ),
        ]
        with self.assertRaises(ValueError):
            self.registry.register_commands(cmds)

    def test_search_commands_matches_name(self):
        self.registry.register_commands([
            CommandInfo(
                name='Calendar', keywords=['agenda'], icon='calendar',
                color='accent', url='/calendar', kind='navigate', module_slug='calendar',
            ),
        ])
        results = self.registry.search_commands('cal')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, 'Calendar')

    def test_search_commands_matches_keyword(self):
        self.registry.register_commands([
            CommandInfo(
                name='Calendar', keywords=['agenda', 'planning'], icon='calendar',
                color='accent', url='/calendar', kind='navigate', module_slug='calendar',
            ),
        ])
        results = self.registry.search_commands('agenda')
        self.assertEqual(len(results), 1)

    def test_search_commands_case_insensitive(self):
        self.registry.register_commands([
            CommandInfo(
                name='Chat', keywords=['messages'], icon='message-circle',
                color='info', url='/chat', kind='navigate', module_slug='chat',
            ),
        ])
        results = self.registry.search_commands('CHAT')
        self.assertEqual(len(results), 1)

    def test_search_commands_no_match(self):
        self.registry.register_commands([
            CommandInfo(
                name='Chat', keywords=['messages'], icon='message-circle',
                color='info', url='/chat', kind='navigate', module_slug='chat',
            ),
        ])
        results = self.registry.search_commands('zzzzz')
        self.assertEqual(len(results), 0)

    def test_search_commands_name_match_before_keyword_match(self):
        self.registry.register_commands([
            CommandInfo(
                name='New event', keywords=['calendar', 'meeting'], icon='calendar-plus',
                color='accent', url='/calendar', kind='action', module_slug='calendar',
            ),
            CommandInfo(
                name='Calendar', keywords=['agenda'], icon='calendar',
                color='accent', url='/calendar', kind='navigate', module_slug='calendar',
            ),
        ])
        results = self.registry.search_commands('calendar')
        self.assertEqual(results[0].name, 'Calendar')
        self.assertEqual(results[1].name, 'New event')

    def test_search_commands_skips_inactive_modules(self):
        self.registry.register(ModuleInfo(
            name='Notes', slug='notes', description='Notes',
            icon='notebook-pen', color='accent', url=None, active=False, order=30,
        ))
        self.registry.register_commands([
            CommandInfo(
                name='Chat', keywords=['chat'], icon='message-circle',
                color='info', url='/chat', kind='navigate', module_slug='chat',
            ),
            CommandInfo(
                name='Notes', keywords=['notes'], icon='notebook-pen',
                color='accent', url='/notes', kind='navigate', module_slug='notes',
            ),
        ])
        results = self.registry.search_commands('n')
        names = [r.name for r in results]
        self.assertNotIn('Notes', names)

    def test_search_commands_returns_empty_when_no_commands(self):
        results = self.registry.search_commands('anything')
        self.assertEqual(results, [])
