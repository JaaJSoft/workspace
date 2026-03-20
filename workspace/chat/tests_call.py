from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APITestCase
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember, Call, CallParticipant, Message

User = get_user_model()


class CallModelTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice', password='pass')
        self.user_b = User.objects.create_user(username='bob', password='pass')
        self.conv = Conversation.objects.create(kind='group', title='Test', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    def test_create_call(self):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a)
        self.assertEqual(call.status, 'ringing')
        self.assertIsNone(call.started_at)
        self.assertIsNone(call.ended_at)

    def test_one_active_call_per_conversation(self):
        Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        with self.assertRaises(IntegrityError):
            Call.objects.create(conversation=self.conv, initiator=self.user_b, status='ringing')

    def test_ended_call_allows_new_call(self):
        Call.objects.create(conversation=self.conv, initiator=self.user_a, status='ended')
        call2 = Call.objects.create(conversation=self.conv, initiator=self.user_b)
        self.assertEqual(call2.status, 'ringing')

    def test_participant_unique_active(self):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        CallParticipant.objects.create(call=call, user=self.user_a)
        with self.assertRaises(IntegrityError):
            CallParticipant.objects.create(call=call, user=self.user_a)

    def test_participant_rejoin_after_leave(self):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        p1 = CallParticipant.objects.create(call=call, user=self.user_a)
        p1.left_at = timezone.now()
        p1.save()
        p2 = CallParticipant.objects.create(call=call, user=self.user_a)
        self.assertIsNone(p2.left_at)


class CallTaskTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice_task', password='pass')
        self.user_b = User.objects.create_user(username='bob_task', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='DM', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    @patch('workspace.core.sse_registry.notify_sse_inline')
    def test_timeout_marks_missed(self, mock_sse):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='ringing')
        CallParticipant.objects.create(call=call, user=self.user_a)
        from workspace.chat.call_tasks import call_timeout
        call_timeout(str(call.uuid))
        call.refresh_from_db()
        self.assertEqual(call.status, 'missed')

    @patch('workspace.core.sse_registry.notify_sse_inline')
    def test_timeout_noop_if_active(self, mock_sse):
        call = Call.objects.create(conversation=self.conv, initiator=self.user_a, status='active')
        from workspace.chat.call_tasks import call_timeout
        call_timeout(str(call.uuid))
        call.refresh_from_db()
        self.assertEqual(call.status, 'active')


class CallServiceTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice_svc', password='pass')
        self.user_b = User.objects.create_user(username='bob_svc', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='DM', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_start_call(self, mock_timeout, mock_push, mock_sse):
        from workspace.chat.call_services import start_call
        call = start_call(conversation=self.conv, initiator=self.user_a)
        self.assertEqual(call.status, 'ringing')
        self.assertTrue(CallParticipant.objects.filter(call=call, user=self.user_a).exists())

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_join_call_activates(self, mock_timeout, mock_sse):
        from workspace.chat.call_services import start_call, join_call
        with patch('workspace.chat.call_services.send_push_notification'):
            call = start_call(conversation=self.conv, initiator=self.user_a)
        join_call(call=call, user=self.user_b)
        call.refresh_from_db()
        self.assertEqual(call.status, 'active')

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_leave_call_ends_when_empty(self, mock_timeout, mock_sse):
        from workspace.chat.call_services import start_call, join_call, leave_call
        with patch('workspace.chat.call_services.send_push_notification'):
            call = start_call(conversation=self.conv, initiator=self.user_a)
        join_call(call=call, user=self.user_b)
        leave_call(call=call, user=self.user_a)
        leave_call(call=call, user=self.user_b)
        call.refresh_from_db()
        self.assertEqual(call.status, 'ended')

    def test_generate_turn_credentials(self):
        from workspace.chat.call_services import generate_ice_servers
        with self.settings(TURN_SECRET='test-secret', TURN_SERVER_URL='turn:example.com:3478', STUN_SERVER_URL='stun:stun.l.google.com:19302'):
            servers = generate_ice_servers()
            self.assertEqual(servers[0]['urls'], 'stun:stun.l.google.com:19302')
            self.assertEqual(servers[1]['urls'], 'turn:example.com:3478')
            self.assertIn('credential', servers[1])


class CallAPITests(APITestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='alice_api', password='pass')
        self.user_b = User.objects.create_user(username='bob_api', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='DM', created_by=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_a)
        ConversationMember.objects.create(conversation=self.conv, user=self.user_b)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_start_call(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.user_a)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp.status_code, 201)
        self.assertIn('call_id', resp.data)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_join_call(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.user_a)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.client.force_authenticate(self.user_b)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/join')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('participants', resp.data)

    def test_outsider_cannot_start_call(self):
        outsider = User.objects.create_user(username='outsider_api', password='pass')
        self.client.force_authenticate(outsider)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp.status_code, 403)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_signal_validates_participants(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.user_a)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.client.force_authenticate(self.user_b)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/join')
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/signal', {'to_user': self.user_a.id, 'type': 'offer', 'payload': {'sdp': 'test'}}, format='json')
        self.assertEqual(resp.status_code, 204)


class CallFlowIntegrationTest(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice_e2e', password='pass')
        self.bob = User.objects.create_user(username='bob_e2e', password='pass')
        User.objects.get_or_create(username='system', defaults={'is_active': False, 'email': 'system@localhost'})
        self.conv = Conversation.objects.create(kind='dm', title='E2E', created_by=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_full_call_flow(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.alice)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp.status_code, 201)
        call_id = resp.data['call_id']

        self.client.force_authenticate(self.bob)
        resp = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/join')
        self.assertEqual(resp.status_code, 200)

        call = Call.objects.get(uuid=call_id)
        self.assertEqual(call.status, 'active')

        resp = self.client.post(
            f'/api/v1/chat/conversations/{self.conv.uuid}/call/signal',
            {'to_user': self.alice.id, 'type': 'offer', 'payload': {'sdp': 'v=0...'}},
            format='json',
        )
        self.assertEqual(resp.status_code, 204)

        resp = self.client.post(
            f'/api/v1/chat/conversations/{self.conv.uuid}/call/mute',
            {'muted': True}, format='json',
        )
        self.assertEqual(resp.status_code, 204)

        self.client.force_authenticate(self.alice)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/leave')
        self.client.force_authenticate(self.bob)
        self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/leave')

        call.refresh_from_db()
        self.assertEqual(call.status, 'ended')

        system_msg = Message.objects.filter(conversation=self.conv, author__username='system').last()
        self.assertIsNotNone(system_msg)
        self.assertIn('Appel vocal', system_msg.body)

    @patch('workspace.chat.call_services.notify_sse_inline')
    @patch('workspace.chat.call_services.send_push_notification')
    @patch('workspace.chat.call_tasks.call_timeout.apply_async')
    def test_cannot_start_two_calls(self, mock_timeout, mock_push, mock_sse):
        self.client.force_authenticate(self.alice)
        resp1 = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp1.status_code, 201)
        resp2 = self.client.post(f'/api/v1/chat/conversations/{self.conv.uuid}/call/start')
        self.assertEqual(resp2.status_code, 409)
