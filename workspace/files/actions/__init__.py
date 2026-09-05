from workspace.common.actions import BaseActionRegistry


class ActionRegistry(BaseActionRegistry):
    """The action set a file or folder can offer.

    ``FilesConfig.ready()`` imports the action modules, in the order that
    keeps each category contiguous in the menu.
    """

    @classmethod
    def applies_to(cls, action, file_obj):
        return file_obj.node_type in action.node_types
