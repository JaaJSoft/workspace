def can_manage_bot_task(user, task):
    """Whether *user* may change or stop *task*, an AgentGoal or ScheduledMessage.

    The bot carries a task out with its creator's identity - its tools reach
    the creator's mail, calendar and files - so only the creator may rewrite or
    stop it. Any other member could otherwise make the bot act as the creator.
    """
    return task.created_by_id == user.id
