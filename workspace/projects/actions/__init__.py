from workspace.common.actions import BaseActionRegistry

from ..models import Task


class ProjectActionRegistry(BaseActionRegistry):
    """The action set a project or a task can offer.

    One registry for both target types: the client asks one endpoint with a
    mixed batch, and ``target_types`` on each action says which it is for.
    ``ProjectsConfig.ready()`` imports the action modules.
    """

    @classmethod
    def applies_to(cls, action, obj):
        target = "task" if isinstance(obj, Task) else "project"
        return target in action.target_types
