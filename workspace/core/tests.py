from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.core.module_registry import PendingActionProviderInfo, ModuleInfo, ModuleRegistry

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
