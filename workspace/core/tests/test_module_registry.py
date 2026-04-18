from django.test import TestCase

from workspace.core.module_registry import (
    CommandInfo,
    ModuleInfo,
    ModuleRegistry,
    PendingActionProviderInfo,
    SearchProviderInfo,
    SearchResult,
)


def _make_module(slug='chat', **kwargs):
    defaults = {
        'name': slug.title(), 'slug': slug, 'description': f'{slug} module',
        'icon': 'icon', 'color': 'primary', 'url': f'/{slug}',
    }
    defaults.update(kwargs)
    return ModuleInfo(**defaults)


class RegisterTests(TestCase):

    def test_register_module(self):
        reg = ModuleRegistry()
        mod = _make_module('chat')
        reg.register(mod)
        self.assertEqual(reg.get('chat'), mod)

    def test_duplicate_slug_raises(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        with self.assertRaises(ValueError):
            reg.register(_make_module('chat'))

    def test_get_returns_none_for_unknown(self):
        reg = ModuleRegistry()
        self.assertIsNone(reg.get('unknown'))

    def test_get_all_sorted_by_order(self):
        reg = ModuleRegistry()
        reg.register(_make_module('b', order=2))
        reg.register(_make_module('a', order=1))
        modules = reg.get_all()
        self.assertEqual([m.slug for m in modules], ['a', 'b'])

    def test_get_active_excludes_inactive(self):
        reg = ModuleRegistry()
        reg.register(_make_module('a', active=True))
        reg.register(_make_module('b', active=False))
        self.assertEqual([m.slug for m in reg.get_active()], ['a'])

    def test_get_for_template_returns_dicts(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        result = reg.get_for_template()
        self.assertIsInstance(result[0], dict)
        self.assertEqual(result[0]['slug'], 'chat')


class SearchProviderTests(TestCase):

    def test_register_search_provider(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        provider = SearchProviderInfo(
            slug='chat-search', module_slug='chat',
            search_fn=lambda q, u, l: [],
        )
        reg.register_search_provider(provider)

    def test_search_provider_requires_registered_module(self):
        reg = ModuleRegistry()
        provider = SearchProviderInfo(
            slug='chat-search', module_slug='chat',
            search_fn=lambda q, u, l: [],
        )
        with self.assertRaises(ValueError):
            reg.register_search_provider(provider)

    def test_duplicate_search_provider_raises(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        provider = SearchProviderInfo(
            slug='chat-search', module_slug='chat',
            search_fn=lambda q, u, l: [],
        )
        reg.register_search_provider(provider)
        with self.assertRaises(ValueError):
            reg.register_search_provider(provider)

    def test_search_calls_providers(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        results = [SearchResult(
            uuid='1', name='Chat', url='/chat', matched_value='Chat',
            match_type='title', type_icon='msg', module_slug='chat',
            module_color='primary',
        )]
        provider = SearchProviderInfo(
            slug='chat-search', module_slug='chat',
            search_fn=lambda q, u, l: results,
        )
        reg.register_search_provider(provider)
        hits = reg.search('test', user=None)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['name'], 'Chat')

    def test_search_skips_inactive_modules(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat', active=False))
        provider = SearchProviderInfo(
            slug='chat-search', module_slug='chat',
            search_fn=lambda q, u, l: [SearchResult(
                uuid='1', name='X', url='/', matched_value='X',
                match_type='t', type_icon='i', module_slug='chat',
                module_color='p',
            )],
        )
        reg.register_search_provider(provider)
        self.assertEqual(reg.search('test', user=None), [])

    def test_search_handles_provider_error(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        provider = SearchProviderInfo(
            slug='chat-search', module_slug='chat',
            search_fn=lambda q, u, l: (_ for _ in ()).throw(RuntimeError('boom')),
        )
        reg.register_search_provider(provider)
        # Should not raise
        hits = reg.search('test', user=None)
        self.assertEqual(hits, [])


class PendingActionProviderTests(TestCase):

    def test_register_pending_action(self):
        reg = ModuleRegistry()
        reg.register(_make_module('mail'))
        provider = PendingActionProviderInfo(
            module_slug='mail', pending_action_fn=lambda u: 5,
        )
        reg.register_pending_action_provider(provider)
        counts = reg.get_pending_action_counts(user=None)
        self.assertEqual(counts['mail'], 5)

    def test_requires_registered_module(self):
        reg = ModuleRegistry()
        with self.assertRaises(ValueError):
            reg.register_pending_action_provider(
                PendingActionProviderInfo(module_slug='mail', pending_action_fn=lambda u: 0)
            )

    def test_duplicate_raises(self):
        reg = ModuleRegistry()
        reg.register(_make_module('mail'))
        reg.register_pending_action_provider(
            PendingActionProviderInfo(module_slug='mail', pending_action_fn=lambda u: 0)
        )
        with self.assertRaises(ValueError):
            reg.register_pending_action_provider(
                PendingActionProviderInfo(module_slug='mail', pending_action_fn=lambda u: 0)
            )

    def test_handles_provider_error(self):
        reg = ModuleRegistry()
        reg.register(_make_module('mail'))
        reg.register_pending_action_provider(
            PendingActionProviderInfo(
                module_slug='mail',
                pending_action_fn=lambda u: (_ for _ in ()).throw(RuntimeError),
            )
        )
        counts = reg.get_pending_action_counts(user=None)
        self.assertEqual(counts['mail'], 0)


class CommandTests(TestCase):

    def _make_cmd(self, name='New Chat', module_slug='chat', **kwargs):
        defaults = {
            'name': name, 'keywords': ['message', 'dm'], 'icon': 'msg',
            'color': 'primary', 'url': '/chat/new', 'kind': 'navigate',
            'module_slug': module_slug,
        }
        defaults.update(kwargs)
        return CommandInfo(**defaults)

    def test_register_commands(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        reg.register_commands([self._make_cmd()])
        self.assertEqual(len(reg.get_active_commands()), 1)

    def test_requires_registered_module(self):
        reg = ModuleRegistry()
        with self.assertRaises(ValueError):
            reg.register_commands([self._make_cmd()])

    def test_get_active_commands_excludes_inactive_modules(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat', active=False))
        reg.register_commands([self._make_cmd()])
        self.assertEqual(reg.get_active_commands(), [])

    def test_search_commands_by_name(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        reg.register_commands([self._make_cmd(name='New Chat')])
        results = reg.search_commands('chat')
        self.assertEqual(len(results), 1)

    def test_search_commands_by_keyword(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        reg.register_commands([self._make_cmd(name='New Chat', keywords=['dm', 'message'])])
        results = reg.search_commands('message')
        self.assertEqual(len(results), 1)

    def test_search_commands_no_match(self):
        reg = ModuleRegistry()
        reg.register(_make_module('chat'))
        reg.register_commands([self._make_cmd()])
        self.assertEqual(reg.search_commands('zzzzz'), [])
