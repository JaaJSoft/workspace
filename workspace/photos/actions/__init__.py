from workspace.common.actions import BaseActionRegistry
from workspace.photos.queries import direct_share_album_ids, get_album_role


class AlbumActionRegistry(BaseActionRegistry):
    """The action set an album can offer.

    ``PhotosConfig.ready()`` imports the action module. The one piece of state
    an action reads, the caller's role in the album, is resolved by the
    endpoint for the whole batch and passed through as ``role``.
    """


def album_action_allowed(action_id, user, album, *, role=None):
    """Whether *user* may run *action_id* on one *album*, resolving what the
    actions read. *role* is the user's role when the caller has it already."""
    return AlbumActionRegistry.is_action_available(
        action_id,
        user,
        album,
        role=role or get_album_role(user, album),
        direct=bool(direct_share_album_ids(user, [album])),
    )


class FaceActionRegistry(BaseActionRegistry):
    """The corrections a face can take, offered on a selection of faces.

    ``PhotosConfig.ready()`` imports the action module. An action reads the
    face row alone, which the endpoint loaded with the batch.
    """
