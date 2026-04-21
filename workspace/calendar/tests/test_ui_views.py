"""Tests for workspace.calendar.ui.views."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.calendar.models import Calendar
from workspace.users.services.settings import set_setting

User = get_user_model()


class CalendarIndexViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='caluser', password='pass123',
        )

    def tearDown(self):
        cache.clear()

    def test_requires_login(self):
        resp = self.client.get('/calendar')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    def test_renders_for_authenticated_user(self):
        self.client.login(username='caluser', password='pass123')
        resp = self.client.get('/calendar')
        self.assertEqual(resp.status_code, 200)

    def test_creates_default_calendar_if_user_has_none(self):
        self.assertFalse(Calendar.objects.filter(owner=self.user).exists())
        self.client.login(username='caluser', password='pass123')
        self.client.get('/calendar')
        self.assertTrue(Calendar.objects.filter(owner=self.user, name='Personal').exists())

    def test_context_has_calendars(self):
        self.client.login(username='caluser', password='pass123')
        resp = self.client.get('/calendar')
        self.assertIn('calendars', resp.context)
        data = resp.context['calendars']
        self.assertIn('owned', data)
        self.assertIn('subscribed', data)
        # Default 'Personal' calendar created on first hit:
        self.assertEqual(len(data['owned']), 1)
        self.assertEqual(data['owned'][0]['name'], 'Personal')

    # ── prefs — server-rendered to avoid double-fetch on init ──
    # The view passes the raw dict; the template renders it via |json_script
    # into <script id="calendar-prefs-data" type="application/json">.

    def test_context_has_prefs_empty_dict_when_no_prefs_stored(self):
        self.client.login(username='caluser', password='pass123')
        resp = self.client.get('/calendar')
        self.assertIn('prefs', resp.context)
        self.assertEqual(resp.context['prefs'], {})

    def test_context_prefs_reflects_stored_settings(self):
        set_setting(self.user, 'calendar', 'preferences', {
            'defaultView': 'agenda',
            'firstDay': 0,
            'weekNumbers': True,
            'timeFormat': '12h',
        })
        self.client.login(username='caluser', password='pass123')
        resp = self.client.get('/calendar')
        prefs = resp.context['prefs']
        self.assertEqual(prefs['defaultView'], 'agenda')
        self.assertEqual(prefs['firstDay'], 0)
        self.assertTrue(prefs['weekNumbers'])
        self.assertEqual(prefs['timeFormat'], '12h')

    def test_prefs_rendered_as_json_script_tag(self):
        """End-to-end: |json_script must inject a parseable <script> tag with the prefs."""
        set_setting(self.user, 'calendar', 'preferences', {'defaultView': 'agenda'})
        self.client.login(username='caluser', password='pass123')
        resp = self.client.get('/calendar')
        self.assertContains(
            resp,
            '<script id="calendar-prefs-data" type="application/json">',
        )
        self.assertContains(resp, '"defaultView": "agenda"')
