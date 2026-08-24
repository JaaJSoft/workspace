import base64
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.ai.tasks.captions import (
    enqueue_caption_if_image,
    enqueue_caption_retry,
    generate_attachment_caption,
)
from workspace.chat.models import Conversation, Message, MessageAttachment

User = get_user_model()

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


def make_attachment(message, name="pic.png", category="image", ai_description=""):
    att = MessageAttachment(
        message=message,
        original_name=name,
        mime_type="image/png",
        type="png",
        category=category,
        size=len(PNG_BYTES),
        ai_description=ai_description,
    )
    att.file.save(name, ContentFile(PNG_BYTES), save=False)
    att.save()
    return att


class CaptionTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="capuser", email="c@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        self.msg = Message.objects.create(
            conversation=self.conv, author=self.user, body="look"
        )

    def test_writes_caption(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.services.llm.call_llm",
            return_value={"content": "A red square on white background."},
        ) as mock_llm:
            generate_attachment_caption(str(att.uuid))
        mock_llm.assert_called_once()
        att.refresh_from_db()
        self.assertEqual(att.ai_description, "A red square on white background.")

    @override_settings(AI_VISION_MODEL="vision-model", AI_MODEL="big-model")
    def test_captions_go_to_the_vision_model(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.services.llm.call_llm",
            return_value={"content": "A red square."},
        ) as mock_llm:
            generate_attachment_caption(str(att.uuid))
        self.assertEqual(mock_llm.call_args.kwargs["model"], "vision-model")

    @override_settings(AI_VISION_MODEL="", AI_MODEL="big-model")
    def test_the_main_model_captions_when_no_vision_model_is_set(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.services.llm.call_llm",
            return_value={"content": "A red square."},
        ) as mock_llm:
            generate_attachment_caption(str(att.uuid))
        self.assertEqual(mock_llm.call_args.kwargs["model"], "big-model")

    def test_idempotent_when_caption_exists(self):
        att = make_attachment(self.msg, ai_description="already here")
        with patch("workspace.ai.services.llm.call_llm") as mock_llm:
            generate_attachment_caption(str(att.uuid))
        mock_llm.assert_not_called()
        att.refresh_from_db()
        self.assertEqual(att.ai_description, "already here")

    def test_llm_failure_leaves_caption_empty(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.services.llm.call_llm", side_effect=RuntimeError("down")
        ):
            generate_attachment_caption(str(att.uuid))  # must not raise
        att.refresh_from_db()
        self.assertEqual(att.ai_description, "")

    def test_skips_non_image(self):
        att = make_attachment(self.msg, name="doc.pdf", category="document")
        with patch("workspace.ai.services.llm.call_llm") as mock_llm:
            generate_attachment_caption(str(att.uuid))
        mock_llm.assert_not_called()

    def test_missing_attachment_does_not_raise(self):
        generate_attachment_caption("00000000-0000-0000-0000-000000000000")


class EnqueueHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="equser", email="eq@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        self.msg = Message.objects.create(
            conversation=self.conv, author=self.user, body="x"
        )

    @override_settings(AI_API_KEY="k")
    def test_enqueues_for_image(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            enqueue_caption_if_image(att)
        mock_delay.assert_called_once_with(str(att.uuid))

    @override_settings(AI_API_KEY="k")
    def test_skips_non_image(self):
        att = make_attachment(self.msg, name="doc.pdf", category="document")
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            enqueue_caption_if_image(att)
        mock_delay.assert_not_called()

    @override_settings(AI_API_KEY="")
    def test_skips_when_ai_not_configured(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            enqueue_caption_if_image(att)
        mock_delay.assert_not_called()


class EnqueueRetryHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="retryuser", email="retry@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        self.msg = Message.objects.create(
            conversation=self.conv, author=self.user, body="x"
        )

    def tearDown(self):
        cache.clear()

    @override_settings(AI_API_KEY="k")
    def test_enqueues_once(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            enqueue_caption_retry(att)
        mock_delay.assert_called_once_with(str(att.uuid))

    @override_settings(AI_API_KEY="k")
    def test_second_call_within_ttl_does_not_enqueue(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            enqueue_caption_retry(att)
            enqueue_caption_retry(att)
        mock_delay.assert_called_once_with(str(att.uuid))

    @override_settings(AI_API_KEY="")
    def test_skips_when_ai_not_configured(self):
        att = make_attachment(self.msg)
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            enqueue_caption_retry(att)
        mock_delay.assert_not_called()

    @override_settings(AI_API_KEY="k")
    def test_skips_non_image(self):
        att = make_attachment(self.msg, name="doc.pdf", category="document")
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            enqueue_caption_retry(att)
        mock_delay.assert_not_called()
