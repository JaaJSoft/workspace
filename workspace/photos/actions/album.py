from workspace.photos.models import Album

from . import AlbumActionRegistry
from .base import ActionCategory, BaseAlbumAction


@AlbumActionRegistry.register
class RenameAlbumAction(BaseAlbumAction):
    id = "rename"
    label = "Rename"
    icon = "pencil"
    category = ActionCategory.EDIT


@AlbumActionRegistry.register
class EditDescriptionAction(BaseAlbumAction):
    id = "edit_description"
    label = "Edit description"
    icon = "align-left"
    category = ActionCategory.EDIT


@AlbumActionRegistry.register
class ChangeSortAction(BaseAlbumAction):
    id = "change_sort"
    icon = "arrow-down-up"
    category = ActionCategory.ORGANIZE

    def get_label(self, obj):
        if obj.sort_mode == Album.SortMode.MANUAL:
            return "Sort by capture date"
        return "Sort manually"

    def get_icon(self, obj):
        if obj.sort_mode == Album.SortMode.MANUAL:
            return "calendar-arrow-down"
        return "grip"


@AlbumActionRegistry.register
class AddItemsAction(BaseAlbumAction):
    id = "add_items"
    label = "Add photos"
    icon = "image-plus"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@AlbumActionRegistry.register
class RemoveItemsAction(BaseAlbumAction):
    id = "remove_items"
    label = "Remove from album"
    icon = "image-minus"
    category = ActionCategory.ORGANIZE
    supports_bulk = True


@AlbumActionRegistry.register
class ReorderAction(BaseAlbumAction):
    id = "reorder"
    label = "Reorder"
    icon = "move"
    category = ActionCategory.ORGANIZE
    supports_bulk = True

    def is_available(self, user, obj, *, role):
        # Positions only show in manual order: a drag in capture order
        # would move nothing the user can see.
        if obj.sort_mode != Album.SortMode.MANUAL:
            return False
        return super().is_available(user, obj, role=role)


@AlbumActionRegistry.register
class SetCoverAction(BaseAlbumAction):
    id = "set_cover"
    label = "Set as cover"
    icon = "image-up"
    category = ActionCategory.ORGANIZE


@AlbumActionRegistry.register
class DeleteAlbumAction(BaseAlbumAction):
    id = "delete"
    label = "Delete album"
    icon = "trash-2"
    category = ActionCategory.DANGER
    css_class = "text-error"
