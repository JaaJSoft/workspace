"""Pausing the tool loop for a human yes/no before an irreversible write.

The tool loop is autonomous: nothing between a model deciding to cancel a
weekly meeting and the rows disappearing. For the handful of tools whose
effect cannot be undone from the chat — a deleted series, a reply already
on its way to an external organiser — the first call stops the round and
puts the question to the user, exactly as ``ask_user_question`` does, and
only a second call carrying ``confirm=True`` writes anything.
"""

CONFIRM_OPTIONS = ["Yes, go ahead", "No, cancel"]


def request_confirmation(context, question):
    """Halt the tool loop and ask *question* as a yes/no the user can click.

    Returns the tool result the model should see. ``setdefault`` keeps the
    first question asked in a round: a second one would overwrite a prompt
    the user is already looking at.
    """
    context.setdefault(
        "question", {"question": question, "options": list(CONFIRM_OPTIONS)}
    )
    context["stop_after_round"] = True
    # Report the question the user is actually looking at, not the one this
    # call proposed: setdefault may have kept an earlier one, and telling the
    # model to await an answer to a prompt nobody was shown invites it to
    # read the reply as approval of the wrong action.
    asked = context["question"]["question"]
    return (
        "Nothing has been changed yet — the user was asked to confirm: "
        f"{asked} Once they agree, repeat this exact call with "
        "confirm=true. If they decline, do not call it again."
    )
