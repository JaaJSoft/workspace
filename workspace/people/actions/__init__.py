from workspace.common.actions import BaseActionRegistry


class PersonActionRegistry(BaseActionRegistry):
    """The action set a person can offer. ``PeopleConfig.ready()`` imports
    the action module so registration happens at boot."""
