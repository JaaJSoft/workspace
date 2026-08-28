class VaultActionRegistry:
    """The action set an entry can offer, declared once at import time.

    ``VaultConfig.ready()`` imports the actions module, so there is no
    lazy-load flag: a registry left empty by a failed import answers exactly
    like an entry the caller cannot reach, and nothing would say which.
    """

    _actions = []

    def __init_subclass__(cls, **kwargs):
        """Give every subclass a registry of its own.

        Without this the class attribute is inherited, so a subclass built to
        hold a throwaway action set would append into the base list instead -
        polluting the real registry for the rest of the process, silently.
        """
        super().__init_subclass__(**kwargs)
        cls._actions = []

    @classmethod
    def register(cls, action_cls):
        """Class decorator - instantiates and stores an action."""
        cls._actions.append(action_cls())
        return action_cls

    @classmethod
    def get_available_actions(
        cls, user, entry, *, role, trashed, schema, present_fields
    ):
        return [
            action.serialize(entry)
            for action in cls._actions
            if action.is_available(
                user,
                entry,
                role=role,
                trashed=trashed,
                schema=schema,
                present_fields=present_fields,
            )
        ]

    @classmethod
    def is_action_available(
        cls, action_id, user, entry, *, role, trashed, schema, present_fields
    ):
        """Whether *action_id* is offered, for the endpoint that performs it.

        Asked instead of restating an action's rules: a menu that offers what
        the endpoint refuses is what two transcriptions of one gate produce
        the first time either is edited.
        """
        for action in cls._actions:
            if action.id == action_id:
                return action.is_available(
                    user,
                    entry,
                    role=role,
                    trashed=trashed,
                    schema=schema,
                    present_fields=present_fields,
                )
        return False

    @classmethod
    def all(cls):
        return list(cls._actions)
