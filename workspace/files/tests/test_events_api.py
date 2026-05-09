"""Tests for GET /api/v1/files/<uuid>/events."""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileEvent, FileShare
from workspace.files.services.events import record_event

User = get_user_model()


class FileEventsListTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', first_name='Alice', email='a@test.com', password='pass',
        )
        self.other_user = User.objects.create_user(
            username='bob', email='b@test.com', password='pass',
        )
        self.client.force_authenticate(user=self.user)

        self.file = File.objects.create(
            owner=self.user, name='doc.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )

    def test_returns_events_newest_first(self):
        record_event(self.file, self.user, FileEvent.Action.CREATED)
        record_event(self.file, self.user, FileEvent.Action.RENAMED, {'old_name': 'a', 'new_name': 'b'})
        record_event(self.file, self.user, FileEvent.Action.SHARED, {'shared_with_username': 'bob'})

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['count'], 3)
        self.assertEqual([e['action'] for e in data['results']], ['shared', 'renamed', 'created'])

    def test_each_event_has_human_label_icon_and_actor(self):
        record_event(self.file, self.user, FileEvent.Action.RENAMED, {'old_name': 'a', 'new_name': 'b'})

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events')

        ev = response.json()['results'][0]
        self.assertEqual(ev['action'], 'renamed')
        self.assertEqual(ev['label'], 'Renamed')
        self.assertEqual(ev['icon'], 'pencil')
        self.assertEqual(ev['actor']['username'], 'alice')
        self.assertEqual(ev['metadata'], {'old_name': 'a', 'new_name': 'b'})

    def test_pagination_limit_and_offset(self):
        for i in range(5):
            record_event(self.file, self.user, FileEvent.Action.RENAMED, {'i': i})

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events?limit=2&offset=1')

        data = response.json()
        self.assertEqual(data['count'], 5)
        self.assertEqual(data['limit'], 2)
        self.assertEqual(data['offset'], 1)
        self.assertEqual(len(data['results']), 2)

    def test_limit_capped_at_maximum(self):
        record_event(self.file, self.user, FileEvent.Action.CREATED)

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events?limit=99999')

        self.assertLessEqual(response.json()['limit'], 200)

    def test_invalid_limit_falls_back_to_default(self):
        record_event(self.file, self.user, FileEvent.Action.CREATED)

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events?limit=garbage')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['limit'], 50)

    def test_unknown_uuid_returns_404(self):
        response = self.client.get('/api/v1/files/00000000-0000-0000-0000-000000000000/events')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_user_cannot_read_events(self):
        record_event(self.file, self.user, FileEvent.Action.RENAMED)
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_shared_user_can_read_events(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
            permission='ro',
        )
        record_event(self.file, self.user, FileEvent.Action.RENAMED, {'old_name': 'a', 'new_name': 'b'})
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['count'], 1)

    def test_events_from_other_files_are_not_included(self):
        other_file = File.objects.create(
            owner=self.user, name='other.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        record_event(self.file, self.user, FileEvent.Action.CREATED)
        record_event(other_file, self.user, FileEvent.Action.CREATED)
        record_event(other_file, self.user, FileEvent.Action.RENAMED)

        response = self.client.get(f'/api/v1/files/{self.file.uuid}/events')

        self.assertEqual(response.json()['count'], 1)
