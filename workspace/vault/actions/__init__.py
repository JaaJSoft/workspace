import importlib


class VaultActionRegistry:
    _actions = []
    _loaded = False

    @classmethod
    def register(cls, action_cls):
        """Class decorator - instantiates and stores an action."""
        cls._actions.append(action_cls())
        return action_cls

    @classmethod
    def get_available_actions(cls, user, entry, *, role, trashed, schema):
        cls._ensure_loaded()
        return [
            action.serialize(entry)
            for action in cls._actions
            if action.is_available(
                user, entry, role=role, trashed=trashed, schema=schema
            )
        ]

    @classmethod
    def all(cls):
        cls._ensure_loaded()
        return list(cls._actions)

    @classmethod
    def _ensure_loaded(cls):
        """Import the action module once, for its registration side effects.

        There is deliberately no reset hook. The registry is process-global
        and the decorators run at import time, so emptying it in one test
        would empty it for every test after - and importing the module again
        would not put anything back, because a second import of an already
        imported module runs nothing. A test that wants an action set of its
        own subclasses this registry instead.
        """
        if cls._loaded:
            return
        cls._loaded = True
        importlib.import_module("workspace.vault.actions.entry")
