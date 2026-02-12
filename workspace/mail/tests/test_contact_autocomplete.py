from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()

URL = '/api/v1/mail/contacts/autocomplete'


class AutocompleteTestMixin:
    """Common setup for contact autocomplete tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='acuser', email='ac@test.com', password='pass123',
        )
        self.other_user = User.objects.create_user(
            username='other', email='other@test.com', password='pass123',
        )

        self.account = MailAccount.objects.create(
            owner=self.user,
            email='me@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='me@example.com',
        )
        self.account.set_password('secret')
        self.account.save()

        self.account2 = MailAccount.objects.create(
            owner=self.user,
            email='me@work.com',
            imap_host='imap.work.com',
            smtp_host='smtp.work.com',
            username='me@work.com',
        )
        self.account2.set_password('secret')
        self.account2.save()

        self.other_account = MailAccount.objects.create(
            owner=self.other_user,
            email='other@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='other@example.com',
        )

        self.inbox = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )
        self.inbox2 = MailFolder.objects.create(
            account=self.account2,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )
        self.other_inbox = MailFolder.objects.create(
            account=self.other_account,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )

    def _create_message(self, folder=None, account=None, imap_uid=1, **kwargs):
        folder = folder or self.inbox
        account = account or self.account
        defaults = {
            'from_address': {'name': 'Sender', 'email': 'sender@example.com'},
            'to_addresses': [{'name': 'Me', 'email': 'me@example.com'}],
            'cc_addresses': [],
            'subject': 'Test',
        }
        defaults.update(kwargs)
        return MailMessage.objects.create(
            account=account,
            folder=folder,
            imap_uid=imap_uid,
            **defaults,
        )


class AuthenticationTests(AutocompleteTestMixin, APITestCase):
    """Unauthenticated requests should be rejected."""

    def test_unauthenticated_returns_403(self):
        resp = self.client.get(URL, {'q': 'test'})
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class QueryValidationTests(AutocompleteTestMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_missing_query_returns_empty(self):
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json(), [])

    def test_single_char_returns_empty(self):
        self._create_message()
        resp = self.client.get(URL, {'q': 'a'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json(), [])

    def test_empty_query_returns_empty(self):
        resp = self.client.get(URL, {'q': ''})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json(), [])

    def test_whitespace_query_returns_empty(self):
        resp = self.client.get(URL, {'q': '  '})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json(), [])


class BasicSearchTests(AutocompleteTestMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_match_from_address_email(self):
        self._create_message(
            from_address={'name': 'Alice', 'email': 'alice@example.com'},
        )
        resp = self.client.get(URL, {'q': 'alice'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['email'], 'alice@example.com')
        self.assertEqual(data[0]['name'], 'Alice')

    def test_match_to_address(self):
        self._create_message(
            to_addresses=[{'name': 'Bob', 'email': 'bob@example.com'}],
        )
        resp = self.client.get(URL, {'q': 'bob'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['email'], 'bob@example.com')

    def test_match_cc_address(self):
        self._create_message(
            cc_addresses=[{'name': 'Carol', 'email': 'carol@example.com'}],
        )
        resp = self.client.get(URL, {'q': 'carol'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['email'], 'carol@example.com')

    def test_match_by_name(self):
        self._create_message(
            from_address={'name': 'Jean-Pierre Dupont', 'email': 'jp@example.com'},
        )
        resp = self.client.get(URL, {'q': 'dupont'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['email'], 'jp@example.com')
        self.assertEqual(data[0]['name'], 'Jean-Pierre Dupont')

    def test_case_insensitive(self):
        self._create_message(
            from_address={'name': 'Alice', 'email': 'Alice@Example.COM'},
        )
        resp = self.client.get(URL, {'q': 'alice'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['email'], 'alice@example.com')

    def test_no_match_returns_empty(self):
        self._create_message(
            from_address={'name': 'Alice', 'email': 'alice@example.com'},
        )
        resp = self.client.get(URL, {'q': 'zzz'})
        self.assertEqual(resp.json(), [])


class DeduplicationTests(AutocompleteTestMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_deduplicates_by_email(self):
        """Same email in multiple messages should appear once."""
        for i in range(5):
            self._create_message(
                imap_uid=100 + i,
                from_address={'name': 'Alice', 'email': 'alice@example.com'},
            )
        resp = self.client.get(URL, {'q': 'alice'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['count'], 5)

    def test_deduplicates_case_insensitive(self):
        """alice@example.com and Alice@Example.COM should be the same contact."""
        self._create_message(
            imap_uid=1,
            from_address={'name': 'Alice', 'email': 'alice@example.com'},
        )
        self._create_message(
            imap_uid=2,
            from_address={'name': 'Alice', 'email': 'Alice@Example.COM'},
        )
        resp = self.client.get(URL, {'q': 'alice'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['count'], 2)

    def test_keeps_most_frequent_name(self):
        """When same email has different names, keep the most frequent one."""
        self._create_message(
            imap_uid=1,
            from_address={'name': 'A. Smith', 'email': 'alex@example.com'},
        )
        self._create_message(
            imap_uid=2,
            from_address={'name': 'Alex Smith', 'email': 'alex@example.com'},
        )
        self._create_message(
            imap_uid=3,
            from_address={'name': 'Alex Smith', 'email': 'alex@example.com'},
        )
        resp = self.client.get(URL, {'q': 'alex'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'Alex Smith')

    def test_no_name_fallback(self):
        """Contacts with no name should have empty string."""
        self._create_message(
            from_address={'name': '', 'email': 'noname@example.com'},
        )
        resp = self.client.get(URL, {'q': 'noname'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], '')


class FrequencySortTests(AutocompleteTestMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_sorted_by_frequency_desc(self):
        # bob appears 3 times, alice appears 1 time
        for i in range(3):
            self._create_message(
                imap_uid=10 + i,
                to_addresses=[{'name': 'Bob', 'email': 'bob@test.com'}],
            )
        self._create_message(
            imap_uid=20,
            to_addresses=[{'name': 'Alice', 'email': 'alice@test.com'}],
        )
        resp = self.client.get(URL, {'q': 'test.com'})
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['email'], 'bob@test.com')
        self.assertEqual(data[0]['count'], 3)
        self.assertEqual(data[1]['email'], 'alice@test.com')
        self.assertEqual(data[1]['count'], 1)

    def test_max_15_results(self):
        for i in range(20):
            self._create_message(
                imap_uid=100 + i,
                from_address={'name': f'User {i}', 'email': f'user{i}@search.com'},
            )
        resp = self.client.get(URL, {'q': 'search.com'})
        data = resp.json()
        self.assertLessEqual(len(data), 15)


class AccountFilterTests(AutocompleteTestMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_filter_by_account_id(self):
        self._create_message(
            imap_uid=1,
            account=self.account,
            folder=self.inbox,
            from_address={'name': 'Alice', 'email': 'alice@filter.com'},
        )
        self._create_message(
            imap_uid=2,
            account=self.account2,
            folder=self.inbox2,
            from_address={'name': 'Bob', 'email': 'bob@filter.com'},
        )

        # Without filter: both appear
        resp = self.client.get(URL, {'q': 'filter.com'})
        self.assertEqual(len(resp.json()), 2)

        # With account filter: only one
        resp = self.client.get(URL, {'q': 'filter.com', 'account_id': str(self.account.uuid)})
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['email'], 'alice@filter.com')

    def test_other_user_messages_not_visible(self):
        """Messages from another user's account should never appear."""
        self._create_message(
            imap_uid=1,
            account=self.other_account,
            folder=self.other_inbox,
            from_address={'name': 'Secret', 'email': 'secret@hidden.com'},
        )
        resp = self.client.get(URL, {'q': 'secret'})
        self.assertEqual(resp.json(), [])


class DeletedMessageTests(AutocompleteTestMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_deleted_messages_excluded(self):
        from django.utils import timezone
        self._create_message(
            from_address={'name': 'Deleted', 'email': 'deleted@example.com'},
            deleted_at=timezone.now(),
        )
        resp = self.client.get(URL, {'q': 'deleted'})
        self.assertEqual(resp.json(), [])


class MultipleFieldMatchTests(AutocompleteTestMixin, APITestCase):
    """Test that contacts are found across from/to/cc in a single message."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_matches_across_fields(self):
        self._create_message(
            from_address={'name': 'From User', 'email': 'from@multi.com'},
            to_addresses=[{'name': 'To User', 'email': 'to@multi.com'}],
            cc_addresses=[{'name': 'Cc User', 'email': 'cc@multi.com'}],
        )
        resp = self.client.get(URL, {'q': 'multi.com'})
        emails = {c['email'] for c in resp.json()}
        self.assertEqual(emails, {'from@multi.com', 'to@multi.com', 'cc@multi.com'})

    def test_post_filter_excludes_non_matching_contacts(self):
        """If a message matches via from_address, to_addresses contacts that
        don't match the query themselves should not appear."""
        self._create_message(
            from_address={'name': 'Alice Target', 'email': 'alice@example.com'},
            to_addresses=[{'name': 'Unrelated', 'email': 'nobody@other.com'}],
        )
        resp = self.client.get(URL, {'q': 'alice'})
        data = resp.json()
        emails = [c['email'] for c in data]
        self.assertIn('alice@example.com', emails)
        self.assertNotIn('nobody@other.com', emails)


class ResponseFormatTests(AutocompleteTestMixin, APITestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def test_response_fields(self):
        self._create_message(
            from_address={'name': 'Test User', 'email': 'test@format.com'},
        )
        resp = self.client.get(URL, {'q': 'test@format'})
        data = resp.json()
        self.assertEqual(len(data), 1)
        contact = data[0]
        self.assertIn('name', contact)
        self.assertIn('email', contact)
        self.assertIn('count', contact)
        self.assertEqual(contact['name'], 'Test User')
        self.assertEqual(contact['email'], 'test@format.com')
        self.assertIsInstance(contact['count'], int)
