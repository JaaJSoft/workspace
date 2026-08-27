from csp.decorators import csp
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.vault.queries import active_identity


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def index(request):
    if active_identity(request.user) is None:
        return redirect("vault_ui:onboarding")
    return render(request, "vault/ui/index.html")


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def onboarding(request):
    # Walking it twice would mint a new salt, and the sealed private keys are
    # the only path back to every VaultKeyWrap the account holds.
    if active_identity(request.user) is not None:
        return redirect("vault_ui:index")
    return render(request, "vault/ui/onboarding.html")
