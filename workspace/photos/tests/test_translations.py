"""The photos module speaks the reader's language: its pages, the labels and
messages its API hands the UI, and the album notifications, each written in
its recipient's language whoever triggered them."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.notifications.models import Notification
from workspace.photos.models import AlbumShare
from workspace.photos.services.album_notifications import (
    ADDITIONS_STREAM,
    announce_additions,
)
from workspace.photos.services.album_sharing import share_album, unshare_album
from workspace.photos.services.albums import add_items, create_album
from workspace.users.services.settings import set_setting

from .images import make_photo

User = get_user_model()


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


class AlbumNotificationLanguageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.camille = User.objects.create_user(username="camille", password="p")
        self.alex = User.objects.create_user(username="alex", password="p")
        set_setting(self.camille, "core", "language", "fr")
        self.album = create_album(self.owner, "Trip")

    def tearDown(self):
        cache.clear()

    def titles(self, user, **filters):
        return list(
            Notification.objects.filter(recipient=user, **filters).values_list(
                "title", "body"
            )
        )

    def test_a_share_is_announced_in_each_members_language(self):
        team = Group.objects.create(name="Team")
        team.user_set.add(self.camille, self.alex)

        share_album(
            self.album,
            role=AlbumShare.Role.CONTRIBUTOR,
            acting_user=self.owner,
            group=team,
        )

        self.assertEqual(
            self.titles(self.camille),
            [
                (
                    "owner a partagé l’album «\u00a0Trip\u00a0» avec vous",
                    "Vous pouvez le parcourir et y ajouter vos propres photos.",
                )
            ],
        )
        self.assertEqual(
            self.titles(self.alex),
            [
                (
                    'owner shared the album "Trip" with you',
                    "You can browse it and add your own photos.",
                )
            ],
        )

    def test_a_new_role_is_a_whole_sentence_in_the_members_language(self):
        share_album(
            self.album, role="viewer", acting_user=self.owner, user=self.camille
        )
        Notification.objects.all().delete()

        share_album(
            self.album, role="manager", acting_user=self.owner, user=self.camille
        )

        self.assertEqual(
            self.titles(self.camille),
            [
                (
                    "Vous êtes désormais gestionnaire de l’album «\u00a0Trip\u00a0»",
                    "Vous pouvez le parcourir, y ajouter des photos et le gérer.",
                )
            ],
        )

    def test_removal_is_announced_in_the_members_language(self):
        share_album(
            self.album, role="viewer", acting_user=self.owner, user=self.camille
        )

        unshare_album(self.album, acting_user=self.owner, user=self.camille)

        self.assertIn(
            ("owner vous a retiré de l’album «\u00a0Trip\u00a0»", ""),
            self.titles(self.camille),
        )

    def test_additions_are_announced_in_each_members_language(self):
        for user in (self.camille, self.alex):
            share_album(self.album, role="viewer", acting_user=self.owner, user=user)
        photos = [make_photo(self.owner, f"{i}.jpg", _at(14)) for i in range(3)]
        add_items(self.album, photos, added_by=self.owner)

        announce_additions(self.album.uuid)

        self.assertEqual(
            self.titles(self.camille, stream=ADDITIONS_STREAM),
            [("owner a ajouté 3 photos à «\u00a0Trip\u00a0»", "")],
        )
        self.assertEqual(
            self.titles(self.alex, stream=ADDITIONS_STREAM),
            [('owner added 3 photos to "Trip"', "")],
        )
