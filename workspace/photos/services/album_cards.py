"""What a listing shows of an album: its title, how many photos the viewer
sees in it, and its cover."""

from dataclasses import dataclass

from django.db.models.functions import Lower

from workspace.files.models import File

from ..queries import album_summaries, user_albums


@dataclass
class AlbumCard:
    album: object
    count: int
    cover: File | None

    @property
    def cover_url(self):
        if self.cover is None or not self.cover.has_thumbnail:
            return ""
        return f"/api/v1/files/{self.cover.uuid}/thumbnail"


def album_cards(user, albums):
    """An ``AlbumCard`` per album of *albums*, in the same order.

    The counts and covers are the viewer's (``queries.album_summaries``).
    Four queries whatever the number of albums.
    """
    albums = list(albums)
    summaries = album_summaries(user, albums)
    cover_ids = {s["cover_id"] for s in summaries.values() if s["cover_id"]}
    covers = File.objects.only("uuid", "has_thumbnail").in_bulk(cover_ids)
    return [
        AlbumCard(
            album=album,
            count=summaries[album.uuid]["count"],
            cover=covers.get(summaries[album.uuid]["cover_id"]),
        )
        for album in albums
    ]


def user_album_cards(user):
    """``album_cards`` for every album *user* can open, by title."""
    return album_cards(user, user_albums(user).order_by(Lower("title"), "uuid"))
