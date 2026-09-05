from workspace.common.actions import BaseActionRegistry


class VaultActionRegistry(BaseActionRegistry):
    """The action set an entry can offer.

    ``VaultConfig.ready()`` imports the action modules. The state an action
    reads - the role, the trash flag, the type's field schema and the field
    ids the row carries - is resolved once per vault by the endpoint and
    passed through as keywords.
    """


class VaultTargetActionRegistry(BaseActionRegistry):
    """The action set a vault itself can offer.

    A second target type rather than a second endpoint: the client asks the
    same URL with ``target="vault"``, and the answer keeps its shape. Opening
    is deliberately absent - navigation is not a permission, and an action
    that answers yes every time it is asked teaches a client nothing.
    """
