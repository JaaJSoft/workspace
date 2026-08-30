from csp.decorators import csp
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.users.services.settings import get_module_settings
from workspace.vault.queries import active_identity
from workspace.vault.types import type_catalogue


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def index(request, vault_uuid=None):
    if active_identity(request.user) is None:
        return redirect("vault_ui:onboarding")
    # Handed to the page, never resolved here. The server cannot read a
    # vault's name, so it has nothing to render from one - and answering 404
    # for a vault out of reach would say it exists in another account.
    return render(
        request,
        "vault/ui/index.html",
        {
            "vault_uuid": vault_uuid,
            "entry_types": type_catalogue(),
            # Read here rather than fetched by the page: the lock delay is
            # applied as the session opens, and a round trip for it would
            # leave the first minutes of every visit on the default.
            "vault_prefs": get_module_settings(request.user, "vault"),
        },
    )


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def onboarding(request):
    # Walking it twice would mint a new salt, and the sealed private keys are
    # the only path back to every VaultKeyWrap the account holds.
    if active_identity(request.user) is not None:
        return redirect("vault_ui:index")
    return render(request, "vault/ui/onboarding.html")
