"""The calendar speaks the reader's language: the page, and the notifications
it sends, each written in its recipient's language rather than the actor's."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.calendar.activity import CalendarActivityProvider
from workspace.calendar.models import Calendar, Event, Poll, PollInvitee, PollSlot
from workspace.calendar.services.recurrence_rule import describe
from workspace.calendar.tasks import notify_today_events
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

from .test_calendar import CalendarTestMixin
from .test_today_notifications import TodayEventNotificationMixin

User = get_user_model()


def _title(user):
    return Notification.objects.get(recipient=user, origin="calendar").title


class CalendarPageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_page_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/calendar")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mes calendriers")
        self.assertContains(response, "Nouvel événement")
        self.assertNotContains(response, "My calendars")

    def test_page_stays_english_by_default(self):
        response = self.client.get("/calendar")
        self.assertContains(response, "My calendars")

    def test_first_visit_names_the_default_calendar_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        self.client.get("/calendar")
        self.assertEqual(Calendar.objects.get(owner=self.user).name, "Personnel")

    def test_default_calendar_stays_english_by_default(self):
        self.client.get("/calendar")
        self.assertEqual(Calendar.objects.get(owner=self.user).name, "Personal")


class EventNotificationLanguageTests(CalendarTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        set_setting(self.member, "core", "language", "fr")

    def tearDown(self):
        cache.clear()

    def test_each_invitee_reads_the_invitation_in_their_language(self):
        self.client.force_authenticate(self.owner)
        start = timezone.now() + timedelta(days=1)
        response = self.client.post(
            "/api/v1/events",
            {
                "calendar_id": str(self.calendar.uuid),
                "title": "Planning",
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "member_ids": [self.member.id, self.outsider.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        member_notif = Notification.objects.get(
            recipient=self.member, origin="calendar"
        )
        self.assertEqual(member_notif.title, "Invitation à « Planning »")
        self.assertEqual(member_notif.body, "owner vous invite à un événement.")
        outsider_notif = Notification.objects.get(
            recipient=self.outsider, origin="calendar"
        )
        self.assertEqual(outsider_notif.title, 'Invited to "Planning"')
        self.assertEqual(outsider_notif.body, "owner invited you to an event.")

    def test_answer_reaches_the_organiser_in_their_language(self):
        set_setting(self.owner, "core", "language", "fr")
        self.client.force_authenticate(self.member)
        response = self.client.post(
            f"/api/v1/events/{self.event.uuid}/respond",
            {"status": "declined"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_title(self.owner), "member a refusé « Team Meeting »")

    def test_cancellation_is_written_in_the_member_language(self):
        self.client.force_authenticate(self.owner)
        response = self.client.delete(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(response.status_code, 204)
        notif = Notification.objects.get(recipient=self.member, origin="calendar")
        self.assertEqual(notif.title, "Annulation de « Team Meeting »")
        self.assertEqual(
            notif.body, "owner a annulé un événement auquel vous participiez."
        )


class PollNotificationLanguageTests(APITestCase):
    def setUp(self):
        self.creator = User.objects.create_user(username="creator", password="pass")
        self.voter = User.objects.create_user(username="voter", password="pass")
        set_setting(self.voter, "core", "language", "fr")
        self.poll = Poll.objects.create(title="Offsite", created_by=self.creator)
        start = timezone.now() + timedelta(days=3)
        for i in range(2):
            PollSlot.objects.create(
                poll=self.poll,
                start=start + timedelta(days=i),
                end=start + timedelta(days=i, hours=1),
                position=i,
            )

    def tearDown(self):
        cache.clear()

    def test_invitation_is_written_in_the_invitee_language(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            f"/api/v1/polls/{self.poll.uuid}/invite",
            {"user_ids": [self.voter.id]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_title(self.voter), "Invitation au sondage « Offsite »")

    def test_vote_reaches_the_creator_in_their_language(self):
        set_setting(self.creator, "core", "language", "fr")
        PollInvitee.objects.create(poll=self.poll, user=self.voter)
        slot = self.poll.slots.first()
        self.client.force_authenticate(self.voter)
        response = self.client.post(
            f"/api/v1/polls/{self.poll.uuid}/vote",
            {"votes": [{"slot_id": str(slot.uuid), "choice": "yes"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        notif = Notification.objects.get(recipient=self.creator, origin="calendar")
        self.assertEqual(notif.title, "Nouveau vote sur « Offsite »")
        self.assertEqual(notif.body, "voter a répondu à votre sondage.")


class TodayReminderLanguageTests(TodayEventNotificationMixin, TestCase):
    def test_reminder_body_follows_each_user_language(self):
        set_setting(self.owner, "core", "language", "fr")
        event = self._event_today()

        notify_today_events()

        notif = self._unread(self.owner, event).get()
        self.assertTrue(notif.body.startswith("Aujourd’hui à "), notif.body)


class ActivityLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alex", password="pass")
        calendar = Calendar.objects.create(name="Work", owner=self.user)
        Event.objects.create(
            calendar=calendar,
            title="Review",
            start=timezone.now() + timedelta(days=1),
            owner=self.user,
        )

    def test_activity_label_follows_the_active_language(self):
        from django.utils import translation

        provider = CalendarActivityProvider()
        with translation.override("fr"):
            events = provider.get_recent_events(self.user.id, viewer_id=None)
        self.assertEqual(events[0]["label"], "Événement créé")


class RecurrenceSummaryLanguageTests(TestCase):
    def test_summary_is_one_french_sentence(self):
        from django.utils import translation

        cases = {
            "RRULE:FREQ=WEEKLY;INTERVAL=2;UNTIL=20270301T000000Z": (
                "Toutes les 2 semaines, jusqu’au 1 mars 2027"
            ),
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR": "Toutes les semaines le lun, mer et ven",
            "RRULE:FREQ=MONTHLY;BYDAY=2TU;COUNT=5": (
                "Le deuxième mardi de chaque mois, pour 5 occurrences"
            ),
        }
        for rule, expected in cases.items():
            with self.subTest(rule=rule), translation.override("fr"):
                self.assertEqual(describe(rule), expected)
