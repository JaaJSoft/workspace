from csp.decorators import csp
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.vault.queries import active_identity


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def index(request, vault_uuid=None):
    if active_identity(request.user) is None:
        return redirect("vault_ui:onboarding")
    # Handed to the page, never resolved here. The server cannot read a
    # vault's name, so it has nothing to render from one - and answering 404
    # for a vault out of reach would say it exists in another account.
    return render(request, "vault/ui/index.html", {"vault_uuid": vault_uuid})


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def onboarding(request):
    # Walking it twice would mint a new salt, and the sealed private keys are
    # the only path back to every VaultKeyWrap the account holds.
    if active_identity(request.user) is not None:
        return redirect("vault_ui:index")
    return render(request, "vault/ui/onboarding.html")
