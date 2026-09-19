from workspace.common.actions import BaseActionRegistry


class PersonActionRegistry(BaseActionRegistry):
    """The action set a person can offer. ``PeopleConfig.ready()`` imports
    the action module so registration happens at boot."""


def actions_for(user, persons):
    """``{uuid: [action]}`` for persons already known to be reachable."""
    has_groups = user.groups.exists()
    return {
        str(person.uuid): PersonActionRegistry.get_available_actions(
            user, person, has_groups=has_groups
        )
        for person in persons
    }
