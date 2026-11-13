"""Chat speaks the reader's language: the API messages its UI shows, the
sidebar it renders, and the notifications it writes for each member."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import translation
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services.calls import (
    _render_system_call_body,
    call_duration_label,
    system_call_label,
)
from workspace.chat.services.notifications import notify_new_message
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()


def _group(title, *users):
    conversation = Conversation.objects.create(
        kind=Conversation.Kind.GROUP, title=title, created_by=users[0]
    )
    for user in users:
        ConversationMember.objects.create(conversation=conversation, user=user)
    return conversation


class ChatPageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.other = User.objects.create_user(username="dominique", password="pass")
        self.conversation = _group("Team", self.user, self.other)
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_page_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get(f"/chat/{self.conversation.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rechercher des conversations")
        self.assertContains(response, "Écrivez un message...")
        self.assertContains(response, "Raccourcis clavier")
        self.assertContains(response, "Entrée")
        self.assertNotContains(response, "Type a message...")
        self.assertNotContains(response, "Search conversations")

    def test_page_stays_english_by_default(self):
        response = self.client.get(f"/chat/{self.conversation.pk}")
        self.assertContains(response, "Type a message...")
        self.assertContains(response, "Search conversations")
        self.assertNotContains(response, "Écrivez un message...")


class NotificationLanguageTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="alice", password="pass")
        self.camille = User.objects.create_user(username="camille", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        set_setting(self.author, "core", "language", "fr")
        set_setting(self.camille, "core", "language", "fr")
        self.conversation = _group("Team", self.author, self.camille, self.bob)

    def tearDown(self):
        cache.clear()

    def _title_for(self, user):
        return Notification.objects.get(recipient=user).title

    def test_each_recipient_reads_the_title_in_their_language(self):
        # The author writes from a French page: the English member must not
        # inherit the author's language.
        with translation.override("fr"):
            notify_new_message(self.conversation, self.author, "bonjour")

        self.assertEqual(self._title_for(self.camille), "alice dans Team")
        self.assertEqual(self._title_for(self.bob), "alice in Team")

    def test_an_english_author_still_writes_french_to_a_french_reader(self):
        with translation.override("en"):
            notify_new_message(self.conversation, self.author, "hello")

        self.assertEqual(self._title_for(self.camille), "alice dans Team")
        self.assertEqual(self._title_for(self.bob), "alice in Team")

    def test_every_language_group_gets_its_notification(self):
        notify_new_message(self.conversation, self.author, "hello")

        self.assertEqual(
            set(
                Notification.objects.filter(conversation=self.conversation).values_list(
                    "recipient_id", flat=True
                )
            ),
            {self.camille.pk, self.bob.pk},
        )


class ApiMessageLanguageTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.other = User.objects.create_user(username="dominique", password="pass")
        self.conversation = _group("Team", self.user, self.other)
        self.outside = _group("Elsewhere", self.other)
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _post_message(self, conversation, body):
        return self.client.post(
            f"/api/v1/chat/conversations/{conversation.pk}/messages",
            {"body": body},
            format="json",
        )

    def test_empty_message_is_refused_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self._post_message(self.conversation, "   ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Le message doit contenir du texte ou au moins un fichier.",
        )

    def test_non_member_is_refused_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self._post_message(self.outside, "bonjour")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Vous n’êtes pas membre de cette conversation.",
        )

    def test_messages_default_to_english(self):
        response = self._post_message(self.outside, "hello")
        self.assertEqual(
            response.json()["detail"], "Not a member of this conversation."
        )


class SidebarLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.other = User.objects.create_user(username="dominique", password="pass")
        self.empty = _group("Quiet", self.user, self.other)
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_empty_conversation_preview_is_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/chat/conversations")
        self.assertContains(response, "Aucun message pour l’instant")
        self.assertNotContains(response, "No messages yet")

    def test_empty_conversation_preview_defaults_to_english(self):
        response = self.client.get("/chat/conversations")
        self.assertContains(response, "No messages yet")

    def test_call_preview_follows_the_reader_not_the_stored_body(self):
        Message.objects.create(
            conversation=self.empty,
            author=self.other,
            body="Call started",
            kind=Message.Kind.SYSTEM,
            tool_data={"type": "call", "state": "active"},
        )
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/chat/conversations")
        self.assertContains(response, "dominique : Appel démarré")


class SystemCallLabelTests(TestCase):
    def test_labels_follow_the_active_language(self):
        ended = {"state": "ended", "duration_seconds": 300, "duration_label": "5 min"}
        with translation.override("fr"):
            self.assertEqual(system_call_label(ended), "Appel terminé - 5 min")
            self.assertEqual(system_call_label({"state": "active"}), "Appel démarré")
        with translation.override("en"):
            self.assertEqual(system_call_label(ended), "Call ended - 5 min")

    def test_duration_is_formatted_for_the_reader_not_as_stored(self):
        ended = {"state": "ended", "duration_seconds": 45, "duration_label": "45s"}
        with translation.override("fr"):
            self.assertEqual(call_duration_label(ended), "45 s")
        with translation.override("en"):
            self.assertEqual(call_duration_label(ended), "45s")

    def test_stored_texts_stay_in_the_source_language(self):
        with translation.override("fr"):
            self.assertEqual(_render_system_call_body("active"), "Call started")
            self.assertEqual(_render_system_call_body("ended", 45), "Call ended - 45s")
