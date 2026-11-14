"""Every view marked ``@prefetchable`` answers a prefetch without leaving a trace.

The browser prefetches a page while the pointer only rests on a link to it, so
the view runs for someone who may never open the page. The decorator is a
promise that the view has no effect a visit would have; this test keeps it.
"""

import re
import uuid
from unittest.mock import patch

from celery.app.task import Task
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.common.prefetch import prefetchable_routes
from workspace.files.models import File

PREFETCH = {"Sec-Purpose": "prefetch"}

_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def _folder(user):
    folder = File.objects.create(
        owner=user, name="Folder", node_type=File.NodeType.FOLDER
    )
    return {"folder": folder.uuid}


# The kwargs to reverse a marked route with a parameter, built for the user of
# the test. A new parameterized prefetchable route adds its case here.
PARAMETER_CASES = {
    "chat_ui:conversation": lambda user: {"conversation_uuid": uuid.uuid4()},
    "files_ui:folder": _folder,
    "users_ui:profile_by_username": lambda user: {"username": user.username},
}


class PrefetchableViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prefetcher", password="pass"
        )
        self.client.force_login(self.user)

    def test_some_views_opted_in(self):
        self.assertIn(("dashboard:index", False), set(prefetchable_routes()))

    def test_every_parameterized_route_has_a_case(self):
        missing = [
            name
            for name, takes_parameters in prefetchable_routes()
            if takes_parameters and name not in PARAMETER_CASES
        ]
        self.assertEqual(
            missing, [], "Add a PARAMETER_CASES entry to visit these routes with."
        )

    def test_a_prefetch_writes_nothing_and_sends_no_task(self):
        for name, takes_parameters in prefetchable_routes():
            kwargs = PARAMETER_CASES[name](self.user) if takes_parameters else None
            url = reverse(name, kwargs=kwargs)
            with self.subTest(url=url):
                # The first visit may set up what a page needs (a home folder,
                # default settings); a prefetch is only promised to add nothing
                # once that is done.
                self.client.get(url)
                with (
                    CaptureQueriesContext(connection) as queries,
                    patch.object(Task, "apply_async") as sent,
                ):
                    response = self.client.get(url, headers=PREFETCH)
                self.assertEqual(response.status_code, 200)
                writes = [q["sql"] for q in queries if _WRITE.match(q["sql"])]
                self.assertEqual(writes, [])
                sent.assert_not_called()
