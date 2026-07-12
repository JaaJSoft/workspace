import importlib

from .base import ActionCategory, BaseProjectAction, NotOnPersonalProjectMixin


class ProjectActionRegistry:
    _actions: list[BaseProjectAction] = []
    _loaded = False

    @classmethod
    def register(cls, action_cls):
        """Class decorator - instantiates and stores an action."""
        instance = action_cls()
        cls._actions.append(instance)
        return action_cls

    @classmethod
    def get_available_actions(cls, user, obj, *, role, archived):
        cls._ensure_loaded()
        from ..models import Task

        target = "task" if isinstance(obj, Task) else "project"
        result = []
        for action in cls._actions:
            if target not in action.target_types:
                continue
            if action.is_available(user, obj, role=role, archived=archived):
                result.append(action.serialize(obj))
        return result

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
            "workspace.projects.actions.project",
            "workspace.projects.actions.task",
        ):
            importlib.import_module(module_name)

    @classmethod
    def _reset(cls):
        """Reset registry state - only for tests."""
        cls._actions = []
        cls._loaded = False


__all__ = [
    "ActionCategory",
    "BaseProjectAction",
    "NotOnPersonalProjectMixin",
    "ProjectActionRegistry",
]
