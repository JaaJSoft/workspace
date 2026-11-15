"""What the ai package shows a user comes out in that user's language.

Nothing here runs inside a French request except the API case: tasks, tool
calls and notifications run in a worker, where no language is active unless
the code activates the user's.
"""

import json
import threading
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone, translation
from django.utils.translation import gettext
from rest_framework.test import APITestCase

from workspace.ai.harness.dispatch import Dispatcher
from workspace.ai.models import AITask, BotProfile
from workspace.ai.prompts.chat import build_chat_messages
from workspace.ai.prompts.editor import build_explain_messages
from workspace.ai.services.responses import handle_generation_error
from workspace.ai.tool_registry import tool_registry
from workspace.calendar.models import Calendar, Event, EventMember
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.mail.models import MailAccount, MailFolder, MailMessageLabel
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

from .harness import StubToolset, call

User = get_user_model()


def _french_user(username):
    user = User.objects.create_user(username=username, password="pw")
    set_setting(user, "core", "language", "fr")
    return user


class ApiErrorTests(APITestCase):
    def tearDown(self):
        cache.clear()

    @patch("workspace.ai.client.is_ai_enabled", return_value=False)
    def test_an_api_error_is_in_the_users_language(self, _enabled):
        # force_login, not force_authenticate: the language is activated by
        # the middleware, which force_authenticate runs before.
        self.client.force_login(_french_user("alice"))

        resp = self.client.post(
            "/api/v1/ai/tasks/mail/summarize",
            {"message_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "L’IA n’est pas configurée.")


class TaskResultTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_a_task_result_is_written_in_its_owners_language(self):
        from workspace.ai.tasks.mail import classify_mail_messages

        task = AITask.objects.create(
            owner=_french_user("alice"),
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": []},
        )

        classify_mail_messages(str(task.pk))

        task.refresh_from_db()
        self.assertEqual(task.result, "Aucun e-mail à classer")

    def test_a_task_error_is_written_in_its_owners_language(self):
        from workspace.ai.tasks.mail import summarize

        task = AITask.objects.create(
            owner=_french_user("alice"),
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={"message_id": "00000000-0000-0000-0000-000000000000"},
        )

        summarize(str(task.pk))

        task.refresh_from_db()
        self.assertEqual(task.status, AITask.Status.FAILED)
        self.assertEqual(task.error, "E-mail introuvable.")


class ToolResultTests(TestCase):
    def setUp(self):
        self.user = _french_user("alice")

    def tearDown(self):
        cache.clear()

    def _dispatcher(self, toolset, concurrency=1):
        return Dispatcher(
            toolset, concurrency=concurrency, user=self.user, bot=None, context={}
        )

    def test_a_tool_refusal_is_in_the_requesting_users_language(self):
        owner = User.objects.create_user(username="bob", password="pw")
        event = Event.objects.create(
            calendar=Calendar.objects.create(name="Bob", owner=owner),
            owner=owner,
            title="Private",
            start=timezone.now() + timedelta(days=1),
        )
        EventMember.objects.create(event=event, user=self.user)
        arguments = json.dumps(
            {"event_id": str(event.uuid), "scope": "all", "title": "Hijacked"}
        )

        outcome = self._dispatcher(tool_registry).run_round(
            [call("c1", name="update_event", arguments=arguments)]
        )

        self.assertIn(
            "Seul le propriétaire peut modifier cet événement.",
            outcome.outcomes[0].result,
        )

    def test_calls_run_in_pool_threads_keep_the_users_language(self):
        # The barrier holds both calls in flight together, so they can only
        # have run in the dispatcher's pool threads, not on this one.
        barrier = threading.Barrier(2, timeout=10)

        def handler(tool_call, context):
            barrier.wait()
            return gettext("Cancel")

        toolset = StubToolset(concurrent={"read_webpage"}, handler=handler)

        outcome = self._dispatcher(toolset, concurrency=2).run_round(
            [call("c1", name="read_webpage"), call("c2", name="read_webpage")]
        )

        self.assertEqual(toolset.peak_in_flight, 2)
        self.assertEqual([o.result for o in outcome.outcomes], ["Annuler"] * 2)


@patch("workspace.notifications.services.notifications.notify_sse")
@patch("workspace.notifications.tasks.send_push_notification.delay")
class NotificationTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_a_label_notification_is_in_its_recipients_language(self, *_mocks):
        from workspace.ai.tasks.mail import _notify_for_notifying_labels
        from workspace.mail.models import MailMessage

        user = _french_user("alice")
        set_setting(user, "mail", "notify_mode", "labels")
        account = MailAccount.objects.create(
            owner=user,
            email="alice@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="alice@test.com",
        )
        folder = MailFolder.objects.create(
            account=account, name="INBOX", folder_type="inbox"
        )
        message = MailMessage.objects.create(
            account=account, folder=folder, imap_uid=1, from_email="x@test.com"
        )
        label = account.labels.get(name="Urgent")
        label.notify_on_apply = True
        task = AITask.objects.create(
            owner=user, task_type=AITask.TaskType.CLASSIFY, input_data={}
        )

        # Activated on purpose: the notification must not take the language
        # the worker happens to have, only its recipient's.
        with translation.override("en"):
            _notify_for_notifying_labels(
                task, [MailMessageLabel(message=message, label=label)]
            )

        self.assertEqual(Notification.objects.get(origin="mail").body, "(sans objet)")

    def test_the_error_reply_is_in_the_requesting_users_language(self, *_mocks):
        user = _french_user("alice")
        bot = User.objects.create_user(username="bot", password="pw")
        BotProfile.objects.create(user=bot)
        conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=user
        )
        ConversationMember.objects.create(conversation=conversation, user=user)
        ConversationMember.objects.create(conversation=conversation, user=bot)
        # A scheduled run: the task belongs to the bot, not the person.
        task = AITask.objects.create(owner=bot, task_type=AITask.TaskType.CHAT)

        handle_generation_error(
            conversation, bot, task, RuntimeError("backend down"), human_user=user
        )

        body = Message.objects.get(conversation=conversation, author=bot).body
        self.assertIn("Désolé, une erreur s’est produite", body)


class ReplyLanguageTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_the_chat_prompt_names_the_users_language(self):
        messages = build_chat_messages("", [], user=_french_user("alice"))

        self.assertIn("The user's interface language is French", messages[0]["content"])

    def test_the_editor_prompt_names_the_users_language(self):
        messages = build_explain_messages("x = 1", user=_french_user("alice"))

        self.assertIn("Write any prose in French", messages[0]["content"])
