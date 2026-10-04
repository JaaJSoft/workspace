"""What a listing shows of an album: its title, how many photos the viewer
sees in it, its cover, and the viewer's role."""

from dataclasses import dataclass

from django.db.models.functions import Lower
from django.urls import reverse

from workspace.files.models import File

from ..queries import album_roles, album_summaries, own_albums, shared_albums


@dataclass
class AlbumCard:
    album: object
    count: int
    cover: File | None
    role: str | None = None

    @property
    def cover_url(self):
        """The cover's thumbnail through the album, which serves it to every
        member, whether or not they can open the file in Files."""
        if self.cover is None or not self.cover.has_thumbnail:
            return ""
        return reverse(
            "photo-album-file-thumbnail", args=[self.album.uuid, self.cover.uuid]
        )


def album_cards(user, albums):
    """An ``AlbumCard`` per album of *albums*, in the same order.

    The counts, covers and roles are the viewer's (``queries.album_summaries``,
    ``queries.album_roles``). A handful of queries whatever the number of
    albums.
    """
    albums = list(albums)
    summaries = album_summaries(user, albums)
    roles = album_roles(user, albums)
    cover_ids = {s["cover_id"] for s in summaries.values() if s["cover_id"]}
    covers = File.objects.only("uuid", "has_thumbnail").in_bulk(cover_ids)
    return [
        AlbumCard(
            album=album,
            count=summaries[album.uuid]["count"],
            cover=covers.get(summaries[album.uuid]["cover_id"]),
            role=roles.get(album.uuid),
        )
        for album in albums
    ]


def _by_title(albums):
    return albums.select_related("owner").order_by(Lower("title"), "uuid")


def own_album_cards(user):
    """``album_cards`` for the albums *user* holds, by title."""
    return album_cards(user, _by_title(own_albums(user)))


def shared_album_cards(user):
    """``album_cards`` for the albums shared with *user*, by title."""
    return album_cards(user, _by_title(shared_albums(user)))


def user_album_cards(user):
    """``album_cards`` for every album *user* can open: their own first,
    then those shared with them, each by title."""
    return own_album_cards(user) + shared_album_cards(user)
