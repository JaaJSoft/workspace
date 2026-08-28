class _ActionRegistry:
    """Registration machinery, shared by the module's registries.

    ``VaultConfig.ready()`` imports the action modules, so there is no
    lazy-load flag: a registry left empty by a failed import answers exactly
    like a row the caller cannot reach, and nothing would say which.

    What each registry adds is its own ``get_available_actions``, because the
    state an action reads differs by target: an entry has a trash flag, a
    field schema and the field ids it carries; a vault has none of the three.
    """

    _actions = []

    def __init_subclass__(cls, **kwargs):
        """Give every subclass a registry of its own.

        Without this the class attribute is inherited, so a subclass built to
        hold a throwaway action set would append into the base list instead -
        polluting the real registry for the rest of the process, silently.
        It is also what keeps the two production registries apart.
        """
        super().__init_subclass__(**kwargs)
        cls._actions = []

    @classmethod
    def register(cls, action_cls):
        """Class decorator - instantiates and stores an action."""
        cls._actions.append(action_cls())
        return action_cls

    @classmethod
    def all(cls):
        return list(cls._actions)


class VaultActionRegistry(_ActionRegistry):
    """The action set an entry can offer."""

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


class VaultTargetActionRegistry(_ActionRegistry):
    """The action set a vault itself can offer.

    A second target type rather than a second endpoint: the client asks the
    same URL with ``target="vault"``, and the answer keeps its shape. Opening
    is deliberately absent - navigation is not a permission, and an action
    that answers yes every time it is asked teaches a client nothing.
    """

    @classmethod
    def get_available_actions(cls, user, vault, *, role):
        return [
            action.serialize(vault)
            for action in cls._actions
            if action.is_available(user, vault, role=role)
        ]
