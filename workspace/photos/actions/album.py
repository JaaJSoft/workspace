from django.utils.translation import gettext_lazy as _

from workspace.photos.models import Album
from workspace.photos.queries import CONTRIBUTOR, MANAGER, OWNER, VIEWER

from . import AlbumActionRegistry
from .base import ActionCategory, BaseAlbumAction


@AlbumActionRegistry.register
class RenameAlbumAction(BaseAlbumAction):
    id = "rename"
    label = _("Rename")
    icon = "pencil"
    category = ActionCategory.EDIT


@AlbumActionRegistry.register
class EditDescriptionAction(BaseAlbumAction):
    id = "edit_description"
    label = _("Edit description")
    icon = "align-left"
    category = ActionCategory.EDIT


@AlbumActionRegistry.register
class ChangeSortAction(BaseAlbumAction):
    id = "change_sort"
    icon = "arrow-down-up"
    category = ActionCategory.ORGANIZE

    def get_label(self, obj):
        if obj.sort_mode == Album.SortMode.MANUAL:
            return _("Sort by capture date")
        return _("Sort manually")

    def get_icon(self, obj):
        if obj.sort_mode == Album.SortMode.MANUAL:
            return "calendar-arrow-down"
        return "grip"


@AlbumActionRegistry.register
class AddItemsAction(BaseAlbumAction):
    id = "add_items"
    label = _("Add photos")
    icon = "image-plus"
    category = ActionCategory.ORGANIZE
    supports_bulk = True
    roles = (OWNER, MANAGER, CONTRIBUTOR)


@AlbumActionRegistry.register
class RemoveItemsAction(BaseAlbumAction):
    """A contributor is offered it too, for the items they added alone: the
    endpoint holds them to it item by item (``services.albums.removable``)."""

    id = "remove_items"
    label = _("Remove from album")
    icon = "image-minus"
    category = ActionCategory.ORGANIZE
    supports_bulk = True
    roles = (OWNER, MANAGER, CONTRIBUTOR)


@AlbumActionRegistry.register
class ReorderAction(BaseAlbumAction):
    id = "reorder"
    label = _("Reorder")
    icon = "move"
    category = ActionCategory.ORGANIZE
    supports_bulk = True

    def is_available(self, user, obj, *, role, direct=False):
        # Positions only show in manual order: a drag in capture order
        # would move nothing the user can see.
        if obj.sort_mode != Album.SortMode.MANUAL:
            return False
        return super().is_available(user, obj, role=role)


@AlbumActionRegistry.register
class SetCoverAction(BaseAlbumAction):
    id = "set_cover"
    label = _("Set as cover")
    icon = "image-up"
    category = ActionCategory.ORGANIZE


@AlbumActionRegistry.register
class ShareAlbumAction(BaseAlbumAction):
    """Manage the album's members and its public links."""

    id = "share"
    label = _("Share")
    icon = "share-2"
    category = ActionCategory.SHARE


@AlbumActionRegistry.register
class DownloadAlbumAction(BaseAlbumAction):
    """The originals, one by one or as a zip. A viewer only gets them while
    the album allows it."""

    id = "download"
    label = _("Download album")
    icon = "download"
    category = ActionCategory.SHARE
    roles = (OWNER, MANAGER, CONTRIBUTOR, VIEWER)

    def is_available(self, user, obj, *, role, direct=False):
        if role == VIEWER and not obj.allow_download:
            return False
        return super().is_available(user, obj, role=role)


@AlbumActionRegistry.register
class LeaveAlbumAction(BaseAlbumAction):
    """Give up a share addressed to the user by name. A share with one of
    their groups or projects is not theirs to give up, and an owner has no
    share to leave."""

    id = "leave"
    label = _("Leave album")
    icon = "log-out"
    category = ActionCategory.DANGER
    css_class = "text-error"
    roles = (MANAGER, CONTRIBUTOR, VIEWER)

    def is_available(self, user, obj, *, role, direct=False):
        return direct and super().is_available(user, obj, role=role)


@AlbumActionRegistry.register
class DeleteAlbumAction(BaseAlbumAction):
    id = "delete"
    label = _("Delete album")
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
