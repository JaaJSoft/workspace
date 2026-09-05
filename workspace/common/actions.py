"""Declarative actions a client can offer on a target, and their registries.

An action is what a context menu lists: an id, a label, an icon, a category,
and a rule deciding whether it is offered for one target to one user. The
rule is pure - everything it reads arrives as keyword state resolved once by
the caller - so an endpoint can evaluate every action for every row of a
listing without a query per row.

Modules subclass both halves. The action base gets the module's own
``is_available`` signature (a file has a permission, a task a role and an
archived flag, a vault entry a trash flag and a field schema); the registry
gets ``applies_to``, the structural gate that says which kind of target an
action is for at all. The registry never flattens those differences: it
passes state through untouched.
"""

from abc import ABC, abstractmethod


class BaseAction(ABC):
    id: str
    label: str
    icon: str
    category: object  # a module's own enum; serialised through ``.value``

    css_class: str = ""
    supports_bulk: bool = False

    @abstractmethod
    def is_available(self, user, obj, **state):
        """Whether the action is offered for ``obj`` to ``user``.

        ``state`` is whatever the module's registry was called with. No
        database query is allowed here: the caller resolved the state once
        and this runs for every action of every row.
        """

    def get_label(self, obj):
        return self.label

    def get_icon(self, obj):
        return self.icon

    def get_css_class(self, obj):
        return self.css_class

    def serialize(self, obj):
        return {
            "id": self.id,
            "label": self.get_label(obj),
            "icon": self.get_icon(obj),
            "category": self.category.value,
            "css_class": self.get_css_class(obj),
            "bulk": self.supports_bulk,
        }


class BaseActionRegistry:
    """Registration machinery shared by every module's action registries.

    A registry is a class, not an instance: actions register at import time
    through the ``register`` class decorator, and each subclass receives an
    empty list of its own. Without that the class attribute would be
    inherited, so a registry built to hold a throwaway action set would
    append into the base list instead and pollute every real registry for
    the rest of the process, silently.

    The order actions register in is the order they are offered in. Menus are
    grouped by category, so a module imports its action modules in the order
    that keeps each category contiguous.
    """

    _actions: list = []
    _by_id: dict = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._actions = []
        cls._by_id = {}

    @classmethod
    def register(cls, action_cls):
        """Class decorator - instantiates and stores an action."""
        instance = action_cls()
        cls._actions.append(instance)
        cls._by_id[instance.id] = instance
        return action_cls

    @classmethod
    def get(cls, action_id):
        return cls._by_id.get(action_id)

    @classmethod
    def all(cls):
        return list(cls._actions)

    @classmethod
    def applies_to(cls, action, obj):
        """Whether ``action`` is for this kind of target at all.

        Evaluated before ``is_available``, which may read attributes only the
        right kind of target carries. The base offers every action to every
        target; a registry serving several target types overrides this.
        """
        return True

    @classmethod
    def get_available_actions(cls, user, obj, **state):
        return [
            action.serialize(obj)
            for action in cls._actions
            if cls.applies_to(action, obj) and action.is_available(user, obj, **state)
        ]

    @classmethod
    def is_action_available(cls, action_id, user, obj, **state):
        """Whether ``action_id`` is offered, for the endpoint that performs it.

        Asked instead of restating an action's rules: a menu that offers what
        the endpoint refuses is what two transcriptions of one gate produce
        the first time either is edited.
        """
        action = cls._by_id.get(action_id)
        if action is None or not cls.applies_to(action, obj):
            return False
        return action.is_available(user, obj, **state)
