from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.chat.services.link_preview import extract_urls, fetch_opengraph
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    LinkPreview,
    Message,
    MessageLinkPreview,
)

User = get_user_model()


class ExtractUrlsTests(TestCase):
    def test_single_url(self):
        urls = extract_urls('Check out https://example.com')
        self.assertEqual(urls, ['https://example.com'])

    def test_multiple_urls(self):
        urls = extract_urls('See https://a.com and https://b.com')
        self.assertEqual(urls, ['https://a.com', 'https://b.com'])

    def test_deduplicates(self):
        urls = extract_urls('https://a.com again https://a.com')
        self.assertEqual(urls, ['https://a.com'])

    def test_strips_trailing_punctuation(self):
        urls = extract_urls('Visit https://example.com.')
        self.assertEqual(urls, ['https://example.com'])

    def test_max_five_urls(self):
        text = ' '.join(f'https://{i}.com' for i in range(10))
        urls = extract_urls(text)
        self.assertEqual(len(urls), 5)

    def test_no_urls(self):
        urls = extract_urls('No links here')
        self.assertEqual(urls, [])

    def test_ignores_non_http(self):
        urls = extract_urls('file:///etc/passwd ftp://x.com')
        self.assertEqual(urls, [])

    def test_url_with_path_and_query(self):
        urls = extract_urls('https://example.com/path?q=1&b=2#frag')
        self.assertEqual(urls, ['https://example.com/path?q=1&b=2#frag'])


class FetchOpengraphTests(TestCase):
    """Tests for OG metadata extraction via trafilatura."""

    def _make_html(self, og_tags: dict, meta_desc: str = '') -> str:
        tags = '<title>Page Title</title>\n'
        for prop, content in og_tags.items():
            tags += f'<meta property="og:{prop}" content="{content}">\n'
        if meta_desc:
            tags += f'<meta name="description" content="{meta_desc}">\n'
        return f'<html><head>{tags}</head><body><p>content</p></body></html>'

    @patch('workspace.chat.services.link_preview._fetch_html')
    def test_extracts_og_tags(self, mock_fetch):
        mock_fetch.return_value = self._make_html({
            'title': 'My Page',
            'description': 'A great page',
            'image': 'https://example.com/img.jpg',
            'site_name': 'Example',
        })

        meta = fetch_opengraph('https://example.com')
        self.assertEqual(meta['title'], 'My Page')
        self.assertEqual(meta['description'], 'A great page')
        self.assertEqual(meta['image'], 'https://example.com/img.jpg')
        self.assertEqual(meta['site_name'], 'Example')

    @patch('workspace.chat.services.link_preview._fetch_html')
    def test_fallback_to_meta_description(self, mock_fetch):
        mock_fetch.return_value = self._make_html({}, meta_desc='Fallback desc')

        meta = fetch_opengraph('https://example.com')
        self.assertIn('Fallback desc', meta['description'])

    @patch('workspace.chat.services.link_preview._fetch_html')
    def test_favicon_fallback(self, mock_fetch):
        mock_fetch.return_value = self._make_html({'title': 'X'})

        meta = fetch_opengraph('https://example.com/page')
        self.assertEqual(meta['favicon'], 'https://example.com/favicon.ico')

    def test_rejects_unsafe_url(self):
        with self.assertRaises(ValueError):
            fetch_opengraph('http://127.0.0.1/admin')

    def test_rejects_private_url(self):
        with self.assertRaises(ValueError):
            fetch_opengraph('http://192.168.1.1/')

    @patch('workspace.chat.services.link_preview._fetch_html')
    def test_returns_empty_strings_for_missing_fields(self, mock_fetch):
        mock_fetch.return_value = '<html><head></head><body></body></html>'

        meta = fetch_opengraph('https://example.com')
        for key in ('title', 'description', 'image', 'site_name'):
            self.assertIsInstance(meta[key], str)


class FetchLinkPreviewsTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title='Test', created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.user,
            body='https://example.com',
            body_html='<p><a href="https://example.com">https://example.com</a></p>',
        )

    @patch('workspace.chat.tasks.fetch_opengraph')
    @patch('workspace.chat.tasks.notify_conversation_members')
    def test_creates_preview_and_links_to_message(self, mock_notify, mock_fetch):
        from workspace.chat.tasks import fetch_link_previews

        mock_fetch.return_value = {
            'title': 'Example',
            'description': 'An example page',
            'image': 'https://example.com/og.jpg',
            'site_name': 'Example.com',
            'favicon': 'https://example.com/favicon.ico',
        }

        fetch_link_previews(str(self.message.pk), ['https://example.com'])

        self.assertEqual(LinkPreview.objects.count(), 1)
        preview = LinkPreview.objects.first()
        self.assertEqual(preview.title, 'Example')
        self.assertFalse(preview.fetch_failed)

        self.assertEqual(MessageLinkPreview.objects.count(), 1)
        mlp = MessageLinkPreview.objects.first()
        self.assertEqual(mlp.message_id, self.message.pk)
        self.assertEqual(mlp.preview_id, preview.pk)
        self.assertEqual(mlp.position, 0)

        mock_notify.assert_called_once()

    @patch('workspace.chat.tasks.fetch_opengraph')
    @patch('workspace.chat.tasks.notify_conversation_members')
    def test_reuses_cached_preview(self, mock_notify, mock_fetch):
        from workspace.chat.tasks import fetch_link_previews

        existing = LinkPreview.objects.create(
            url='https://example.com',
            title='Cached',
        )

        fetch_link_previews(str(self.message.pk), ['https://example.com'])

        mock_fetch.assert_not_called()
        self.assertEqual(LinkPreview.objects.count(), 1)
        self.assertEqual(MessageLinkPreview.objects.count(), 1)
        self.assertEqual(MessageLinkPreview.objects.first().preview_id, existing.pk)

    @patch('workspace.chat.tasks.fetch_opengraph')
    @patch('workspace.chat.tasks.notify_conversation_members')
    def test_marks_failed_on_error(self, mock_notify, mock_fetch):
        from workspace.chat.tasks import fetch_link_previews

        mock_fetch.side_effect = Exception('Connection refused')

        fetch_link_previews(str(self.message.pk), ['https://bad.com'])

        preview = LinkPreview.objects.get(url='https://bad.com')
        self.assertTrue(preview.fetch_failed)
        self.assertEqual(MessageLinkPreview.objects.count(), 0)

    @patch('workspace.chat.tasks.fetch_opengraph')
    @patch('workspace.chat.tasks.notify_conversation_members')
    def test_skips_previously_failed(self, mock_notify, mock_fetch):
        from workspace.chat.tasks import fetch_link_previews

        LinkPreview.objects.create(url='https://bad.com', fetch_failed=True)

        fetch_link_previews(str(self.message.pk), ['https://bad.com'])

        mock_fetch.assert_not_called()
        self.assertEqual(MessageLinkPreview.objects.count(), 0)


from workspace.chat.serializers import MessageSerializer


class MessageSerializerPreviewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ser', password='p')
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user,
        )
        self.message = Message.objects.create(
            conversation=self.conv, author=self.user,
            body='hi', body_html='<p>hi</p>',
        )

    def test_serializes_link_previews(self):
        preview = LinkPreview.objects.create(
            url='https://example.com',
            title='Example',
            description='Desc',
            image_url='https://example.com/img.jpg',
            site_name='Example.com',
            favicon_url='https://example.com/favicon.ico',
        )
        MessageLinkPreview.objects.create(
            message=self.message, preview=preview, position=0,
        )
        msg = Message.objects.prefetch_related(
            'link_previews__preview', 'reactions__user', 'attachments',
        ).get(pk=self.message.pk)
        data = MessageSerializer(msg).data
        self.assertIn('link_previews', data)
        self.assertEqual(len(data['link_previews']), 1)
        lp = data['link_previews'][0]
        self.assertEqual(lp['url'], 'https://example.com')
        self.assertEqual(lp['title'], 'Example')

    def test_empty_when_no_previews(self):
        msg = Message.objects.prefetch_related(
            'link_previews__preview', 'reactions__user', 'attachments',
        ).get(pk=self.message.pk)
        data = MessageSerializer(msg).data
        self.assertEqual(data['link_previews'], [])


class LinkPreviewIntegrationTests(APITestCase):
    """Test the full flow: send message -> task runs -> previews appear in API."""

    def setUp(self):
        self.user = User.objects.create_user(username='integ', password='pass123')
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title='Test', created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        self.client.force_authenticate(self.user)

    @patch('workspace.chat.tasks.fetch_opengraph')
    def test_send_message_triggers_preview_and_appears_in_api(self, mock_fetch):
        mock_fetch.return_value = {
            'title': 'GitHub',
            'description': 'Where the world builds software',
            'image': 'https://github.githubassets.com/images/og.png',
            'site_name': 'GitHub',
            'favicon': 'https://github.com/favicon.ico',
        }

        # Send a message with a URL
        resp = self.client.post(
            f'/api/v1/chat/conversations/{self.conv.pk}/messages',
            {'body': 'Check this out https://github.com'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        message_uuid = resp.data['uuid']

        # In test mode (CELERY_TASK_ALWAYS_EAGER=True), the task runs synchronously
        # So the preview should already exist
        self.assertEqual(LinkPreview.objects.count(), 1)
        self.assertEqual(MessageLinkPreview.objects.count(), 1)

        # Verify the preview appears in the message list API
        resp = self.client.get(f'/api/v1/chat/conversations/{self.conv.pk}/messages')
        self.assertEqual(resp.status_code, 200)
        messages = resp.data['messages']
        msg = next(m for m in messages if m['uuid'] == str(message_uuid))
        self.assertEqual(len(msg['link_previews']), 1)
        self.assertEqual(msg['link_previews'][0]['title'], 'GitHub')
