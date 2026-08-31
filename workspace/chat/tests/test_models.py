from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import (
    CallParticipant,
    CallSession,
    Conversation,
    ConversationMember,
    Meeting,
    MeetingGuest,
    Message,
    MessageAttachment,
    MessageInteraction,
)

User = get_user_model()


class MessageInteractionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pw",
        )
        self.bot = User.objects.create_user(
            username="bot",
            email="b@test.com",
            password="pw",
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.bot,
            body="Pick a tone:",
        )

    def test_create_question_interaction(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Pick a tone", "options": ["Formal", "Casual"]},
        )
        self.assertEqual(interaction.kind, "question")
        self.assertEqual(interaction.payload["options"], ["Formal", "Casual"])
        self.assertIsNone(interaction.interacted_at)
        self.assertIsNone(interaction.state)

    def test_one_to_one_constraint(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "q", "options": ["a", "b"]},
        )
        with self.assertRaises(IntegrityError):
            MessageInteraction.objects.create(
                message=self.message,
                kind=MessageInteraction.Kind.QUESTION,
                payload={"question": "q2", "options": ["x", "y"]},
            )

    def test_cascade_delete_with_message(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "q", "options": ["a", "b"]},
        )
        self.message.delete()
        self.assertEqual(MessageInteraction.objects.count(), 0)

    def test_reverse_accessor_on_message(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "q", "options": ["a", "b"]},
        )
        self.message.refresh_from_db()
        self.assertEqual(self.message.interaction, interaction)

    def test_interacted_by_set_null_on_user_delete(self):
        # Use an outsider user so deleting them does not cascade through
        # ConversationMember.user / Conversation.created_by / Message.author
        # and wipe the interaction before we can check it.
        outsider = User.objects.create_user(
            username="outsider",
            email="o@test.com",
            password="pw",
        )
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "q", "options": ["a", "b"]},
        )
        interaction.interacted_by = outsider
        interaction.interacted_at = None  # leave unanswered
        interaction.save()
        outsider.delete()
        interaction.refresh_from_db()
        self.assertIsNone(interaction.interacted_by)


class MessageAttachmentSplitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        self.message = Message.objects.create(
            conversation=self.conv, author=self.user, body="media"
        )

    def _attach(self, name, mime, category="unknown", viewer="", type=""):
        return MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(name, b"x", content_type=mime),
            original_name=name,
            mime_type=mime,
            type=type,
            category=category,
            viewer=viewer,
            size=1,
        )

    def test_media_and_file_attachments_split(self):
        image = self._attach("photo.png", "image/png", category="image")
        video = self._attach("clip.mp4", "video/mp4", category="video")
        doc = self._attach("doc.pdf", "application/pdf", category="document")
        self.assertEqual(self.message.media_attachments, [image, video])
        self.assertEqual(self.message.file_attachments, [doc])

    def test_split_falls_back_to_mime_type_for_unknown_category(self):
        image = self._attach("photo.jpg", "image/jpeg")
        doc = self._attach("doc.pdf", "application/pdf")
        self.assertEqual(self.message.media_attachments, [image])
        self.assertEqual(self.message.file_attachments, [doc])

    def test_split_reuses_prefetched_attachments(self):
        self._attach("photo.png", "image/png", category="image")
        msg = Message.objects.prefetch_related("attachments").get(
            uuid=self.message.uuid
        )
        with self.assertNumQueries(0):
            self.assertEqual(len(msg.media_attachments), 1)
            self.assertEqual(msg.file_attachments, [])

    def test_pinned_audio_leaves_the_media_bucket(self):
        """A recorded voice message is category=video and viewer=audio. Without
        the exclusion it would render twice in the same bubble."""
        voice = self._attach(
            "voice.webm", "video/webm", category="video", viewer="audio", type="webm"
        )
        self.assertEqual([a.uuid for a in self.message.audio_attachments], [voice.uuid])
        self.assertEqual(self.message.media_attachments, [])
        self.assertEqual(self.message.file_attachments, [])

    def test_audio_bucket_also_accepts_a_derived_viewer(self):
        """A shared .mp3 carries no pin; the viewer comes from its content type."""
        song = self._attach("song.mp3", "audio/mpeg", category="audio", type="mp3")
        self.assertEqual(song.viewer, "")
        self.assertEqual(song.effective_viewer, "audio")
        self.assertEqual([a.uuid for a in self.message.audio_attachments], [song.uuid])

    def test_attachment_buckets_form_a_strict_partition(self):
        image = self._attach("photo.png", "image/png", category="image", type="png")
        video = self._attach("clip.mp4", "video/mp4", category="video", type="mp4")
        voice = self._attach(
            "voice.webm", "video/webm", category="video", viewer="audio", type="webm"
        )
        doc = self._attach(
            "doc.pdf", "application/pdf", category="document", type="pdf"
        )

        media = {a.uuid for a in self.message.media_attachments}
        audios = {a.uuid for a in self.message.audio_attachments}
        files = {a.uuid for a in self.message.file_attachments}

        self.assertEqual(media, {image.uuid, video.uuid})
        self.assertEqual(audios, {voice.uuid})
        self.assertEqual(files, {doc.uuid})
        self.assertEqual(
            media | audios | files, {image.uuid, video.uuid, voice.uuid, doc.uuid}
        )
        self.assertEqual(media & audios, set())
        self.assertEqual(media & files, set())
        self.assertEqual(audios & files, set())

    def test_duration_seconds_defaults_to_null(self):
        att = self._attach("song.mp3", "audio/mpeg", category="audio", type="mp3")
        att.refresh_from_db()
        self.assertIsNone(att.duration_seconds)

    def test_duration_seconds_round_trips(self):
        att = self._attach("voice.webm", "video/webm", viewer="audio", type="webm")
        att.duration_seconds = 12.5
        att.save(update_fields=["duration_seconds"])
        att.refresh_from_db()
        self.assertAlmostEqual(att.duration_seconds, 12.5)


class CallModelTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_user(username="caller", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )

    def test_message_kind_defaults_to_user(self):
        msg = Message.objects.create(
            conversation=self.conv, author=self.user, body="hi"
        )
        self.assertEqual(msg.kind, Message.Kind.USER)

    def test_only_one_active_session_per_conversation(self):
        CallSession.objects.create(conversation=self.conv, started_by=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CallSession.objects.create(conversation=self.conv, started_by=self.user)

    def test_ended_sessions_do_not_collide(self):
        CallSession.objects.create(
            conversation=self.conv,
            started_by=self.user,
            state=CallSession.State.ENDED,
        )
        # A second ended session and a fresh active one are both allowed.
        CallSession.objects.create(
            conversation=self.conv,
            started_by=self.user,
            state=CallSession.State.ENDED,
        )
        CallSession.objects.create(conversation=self.conv, started_by=self.user)

    def test_participant_unique_per_session(self):
        session = CallSession.objects.create(
            conversation=self.conv, started_by=self.user
        )
        CallParticipant.objects.create(session=session, user=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CallParticipant.objects.create(session=session, user=self.user)


class CallParticipantKeyTests(TestCase):
    def test_participant_key_is_the_member_key(self):
        User = get_user_model()
        user = User.objects.create_user(username="pk", password="x")
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=user
        )
        session = CallSession.objects.create(conversation=conv, started_by=user)
        participant = CallParticipant.objects.create(session=session, user=user)
        self.assertEqual(participant.participant_key, f"u:{user.id}")


class GuestIdentityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="gi", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        self.session = CallSession.objects.create(
            conversation=self.conv, started_by=self.user
        )
        cal = Calendar.objects.create(name="C", owner=self.user)
        event = Event.objects.create(
            calendar=cal, owner=self.user, title="E", start=timezone.now()
        )
        meeting_conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        self.meeting = Meeting.objects.create(
            event=event, conversation=meeting_conv, created_by=self.user
        )
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            occurrence_start=timezone.now(),
            token_hash="c" * 64,
        )

    def test_call_session_starts_unlocked(self):
        self.assertFalse(self.session.locked)

    def test_guest_participant_key(self):
        p = CallParticipant.objects.create(session=self.session, guest=self.guest)
        self.assertEqual(p.participant_key, f"g:{self.guest.uuid}")

    def test_member_participant_key_unchanged(self):
        p = CallParticipant.objects.create(session=self.session, user=self.user)
        self.assertEqual(p.participant_key, f"u:{self.user.id}")

    def test_participant_needs_exactly_one_identity(self):
        with self.assertRaises(IntegrityError):
            CallParticipant.objects.create(session=self.session)

    def test_participant_rejects_both_identities(self):
        with self.assertRaises(IntegrityError):
            CallParticipant.objects.create(
                session=self.session, user=self.user, guest=self.guest
            )

    def test_message_needs_exactly_one_identity(self):
        with self.assertRaises(IntegrityError):
            Message.objects.create(conversation=self.conv, body="x")

    def test_guest_message_is_allowed(self):
        msg = Message.objects.create(
            conversation=self.conv, guest=self.guest, body="hello"
        )
        self.assertIsNone(msg.author)
        self.assertEqual(msg.guest_id, self.guest.uuid)

    def test_a_guest_can_be_in_a_session_only_once(self):
        CallParticipant.objects.create(session=self.session, guest=self.guest)
        with self.assertRaises(IntegrityError):
            CallParticipant.objects.create(session=self.session, guest=self.guest)

    def test_a_member_can_be_in_a_session_only_once(self):
        CallParticipant.objects.create(session=self.session, user=self.user)
        with self.assertRaises(IntegrityError):
            CallParticipant.objects.create(session=self.session, user=self.user)
