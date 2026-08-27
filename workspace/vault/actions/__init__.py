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
        if cls._loaded:
            return
        cls._loaded = True
        importlib.import_module("workspace.vault.actions.entry")

    @classmethod
    def _reset(cls):
        """Empty the registry and stop it re-importing - only for tests.

        _loaded stays true on purpose, unlike the projects registry: a test
        that registered its own actions does not want the production set
        loaded on top of them.
        """
        cls._actions = []
        cls._loaded = True
