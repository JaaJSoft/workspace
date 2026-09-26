from workspace.common.actions import BaseActionRegistry


class AlbumActionRegistry(BaseActionRegistry):
    """The action set an album can offer.

    ``PhotosConfig.ready()`` imports the action module. The one piece of state
    an action reads, the caller's role in the album, is resolved by the
    endpoint for the whole batch and passed through as ``role``.
    """
