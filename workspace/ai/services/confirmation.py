"""Pausing the tool loop for a human yes/no before an irreversible write.

The tool loop is autonomous: nothing between a model deciding to cancel a
weekly meeting and the rows disappearing. For the handful of tools whose
effect cannot be undone from the chat — a deleted series, a reply already
on its way to an external organiser — the first call stops the round and
puts the question to the user, exactly as ``ask_user_question`` does, and
only a second call carrying ``confirm=True`` writes anything.

``request_confirmation`` halts the round and trusts the model to repeat the
same call. That is enough when a wrong repeat is recoverable, but it binds
nothing: the confirming call is a fresh response generation, and nothing
proves it carries the arguments the user approved.

``request_bound_confirmation`` closes that gap for the writes where it
matters. It stores the action's payload server-side under a single-use
token and hands the model nothing but the token; the confirming call is
executed from what was stored, so the model cannot alter between the two
rounds what the user was shown. Use it wherever a changed argument on the
second call would be worse than no confirmation at all — an email is the
worked example: the recipients are the whole point of the question.
"""

import uuid

from django.core.cache import cache

CONFIRM_OPTIONS = ["Yes, go ahead", "No, cancel"]

# How long a pinned action stays redeemable. Long enough for the user to
# read the question and answer it, short enough that a confirmation they
# walked away from cannot be cashed in an hour later.
PENDING_TTL = 600


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


def _pending_key(action, token):
    return f"ai:confirm:{action}:{token}"


def request_bound_confirmation(
    context, question, action, user, conversation_id, payload, options=None
):
    """Halt the round and pin *payload* to a single-use confirmation token.

    *action* names the kind of write ("mail.send"), so a token minted for
    one cannot redeem another. The pin is scoped to the user and the
    conversation the question was asked in.

    Returns ``(token, blocked)``. ``blocked`` is a tool result to return
    as-is when another question already holds the round: nothing is stored
    in that case, because a token for a prompt nobody was shown is worse
    than no token — the user's answer to the other question would look like
    approval of this one.
    """
    proposed = {"question": question, "options": list(options or CONFIRM_OPTIONS)}
    context.setdefault("question", proposed)
    context["stop_after_round"] = True
    if context["question"] is not proposed:
        return None, (
            "Another question is already waiting for the user's answer: "
            f"{context['question']['question']} Nothing was prepared for this "
            "call — make it again once they have replied."
        )

    token = uuid.uuid4().hex
    cache.set(
        _pending_key(action, token),
        {
            "user_id": user.pk,
            "conversation_id": str(conversation_id) if conversation_id else None,
            "payload": payload,
        },
        PENDING_TTL,
    )
    return token, None


def consume_bound_confirmation(action, user, conversation_id, token):
    """Return the payload pinned under *token*, once. None if it cannot be.

    Unknown, expired, already redeemed, minted for another action, another
    user or another conversation all answer the same way: the caller cannot
    tell them apart, and neither should the model.
    """
    if not token:
        return None
    pending = cache.get(_pending_key(action, token))
    if not pending:
        return None
    if pending["user_id"] != user.pk:
        return None
    if pending["conversation_id"] != (
        str(conversation_id) if conversation_id else None
    ):
        return None
    cache.delete(_pending_key(action, token))
    return pending["payload"]
