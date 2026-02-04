import importlib

from .base import ActionCategory, BaseAction


class ActionRegistry:
    _actions: list[BaseAction] = []
    _by_id: dict[str, BaseAction] = {}
    _loaded = False

    @classmethod
    def register(cls, action_cls):
        """Class decorator — instantiates and stores an action."""
        instance = action_cls()
        cls._actions.append(instance)
        cls._by_id[instance.id] = instance
        return action_cls

    @classmethod
    def get(cls, action_id):
        cls._ensure_loaded()
        return cls._by_id.get(action_id)

    @classmethod
    def get_available_actions(cls, user, file_obj, *, is_owner, share_permission=None):
        cls._ensure_loaded()
        result = []
        for action in cls._actions:
            if file_obj.node_type not in action.node_types:
                continue
            if action.is_available(user, file_obj, is_owner=is_owner, share_permission=share_permission):
                result.append(action.serialize(file_obj))
        return result

    @classmethod
    def is_action_available(cls, action_id, user, file_obj, *, is_owner, share_permission=None):
        cls._ensure_loaded()
        action = cls._by_id.get(action_id)
        if not action:
            return False
        if file_obj.node_type not in action.node_types:
            return False
        return action.is_available(user, file_obj, is_owner=is_owner, share_permission=share_permission)

    @classmethod
    def all(cls):
        cls._ensure_loaded()
        return list(cls._actions)

    @classmethod
    def _ensure_loaded(cls):
        if cls._loaded:
            return
        cls._loaded = True
        for module_name in (
            'workspace.files.actions.open',
            'workspace.files.actions.transfer',
            'workspace.files.actions.organize',
            'workspace.files.actions.edit',
            'workspace.files.actions.info',
            'workspace.files.actions.danger',
            'workspace.files.actions.trash',
        ):
            importlib.import_module(module_name)

    @classmethod
    def _reset(cls):
        """Reset registry state — only for tests."""
        cls._actions = []
        cls._by_id = {}
        cls._loaded = False


__all__ = ['ActionRegistry', 'ActionCategory', 'BaseAction']
