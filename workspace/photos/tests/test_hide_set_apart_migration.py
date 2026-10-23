"""The data migration that hides the people found by their body who were set
apart from someone their photo already shows."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.migrations import schema_editor_stub
from workspace.photos.models import FaceCluster

from .appearances import BESIDE, BLUE, RED, contact, named_face, photo_at, seen

migration = importlib.import_module(
    "workspace.photos.migrations.0025_hide_people_set_apart_in_their_photo"
)

User = get_user_model()

# Where a face sits on the head of the person ``seen`` puts BESIDE.
FACE_BESIDE = (0.65, 0.1, 0.1, 0.1)


class HidePeopleSetApartTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.lea = contact(self.user, "Léa")
        self.noah = contact(self.user, "Noah")
        self.photo = photo_at(self.user, "party.jpg")

    def run_migration(self):
        migration.hide_people_set_apart_in_their_photo(apps, schema_editor_stub())

    def hidden(self, row):
        row.refresh_from_db()
        return row.hidden

    def test_set_apart_from_a_person_whose_face_is_there(self):
        named_face(self.user, self.photo, self.lea, box=FACE_BESIDE)
        seen(self.user, self.photo, RED, box=BESIDE)
        row = seen(self.user, self.photo, BLUE, rejected_person=self.lea)

        self.run_migration()

        self.assertTrue(self.hidden(row))

    def test_set_apart_from_a_person_somebody_else_there_was_said_to_be(self):
        seen(self.user, self.photo, RED, box=BESIDE, person=self.lea)
        row = seen(self.user, self.photo, BLUE, rejected_person=self.lea)

        self.run_migration()

        self.assertTrue(self.hidden(row))

    def test_said_not_to_be_a_person_the_photo_does_not_show(self):
        named_face(self.user, self.photo, self.noah, box=FACE_BESIDE)
        row = seen(self.user, self.photo, BLUE, rejected_person=self.lea)

        self.run_migration()

        self.assertFalse(self.hidden(row))

    def test_a_face_of_a_hidden_group_shows_nobody(self):
        face = named_face(self.user, self.photo, self.lea, box=FACE_BESIDE)
        FaceCluster.objects.filter(pk=face.cluster_id).update(hidden=True)
        row = seen(self.user, self.photo, BLUE, rejected_person=self.lea)

        self.run_migration()

        self.assertFalse(self.hidden(row))

    def test_people_named_or_suggested_since_are_left_alone(self):
        named_face(self.user, self.photo, self.lea, box=FACE_BESIDE)
        said = seen(
            self.user, self.photo, BLUE, person=self.noah, rejected_person=self.lea
        )
        suggested = seen(
            self.user,
            self.photo,
            BLUE,
            suggested_person=self.noah,
            rejected_person=self.lea,
        )

        self.run_migration()

        self.assertFalse(self.hidden(said))
        self.assertFalse(self.hidden(suggested))
