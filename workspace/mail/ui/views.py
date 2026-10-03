from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.mail.models import MailAccount
from workspace.mail.serializers import MailAccountSerializer
from workspace.mail.services.ai_settings import (
    MAIL_AI_FEATURES,
    is_mail_ai_feature_enabled,
)
from workspace.mail.services.notifications import (
    resolve_notify_burst,
    resolve_notify_mode,
)
from workspace.mail.services.oauth2 import get_available_providers
from workspace.people.queries import person_with_email, user_persons
from workspace.users.queries import active_user_with_email


@login_required
@ensure_csrf_cookie
def index(request):
    accounts = MailAccount.objects.filter(owner=request.user, is_active=True)

    return render(
        request,
        "mail/ui/index.html",
        {
            "accounts": MailAccountSerializer(accounts, many=True).data,
            "oauth_providers": get_available_providers(),
            "mail_ai_features": {
                f: is_mail_ai_feature_enabled(request.user, f) for f in MAIL_AI_FEATURES
            },
            "notify_mode": resolve_notify_mode(request.user),
            "notify_max_burst": resolve_notify_burst(request.user),
        },
    )


@login_required
def contact_card(request):
    """The hover card of an address in a message header.

    Shows the address book's person for it when there is one, and offers to
    add it otherwise - linked to the workspace account that carries it, if any.
    """
    email = request.GET.get("email", "").strip()[:254]
    if not email:
        return HttpResponseBadRequest()
    name = request.GET.get("name", "").strip()[:255]
    person = person_with_email(user_persons(request.user), email)
    account = None
    if person is None or person.linked_user_id is None:
        account = active_user_with_email(email)
    return render(
        request,
        "mail/ui/partials/contact_card.html",
        {"email": email, "name": name, "person": person, "account": account},
    )
