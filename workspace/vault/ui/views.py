from csp.decorators import csp
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.vault.models import AccountIdentity


def _has_active_identity(user):
    """Whether *user* finished onboarding.

    A pending row does not count: ``init`` created it and the browser never
    came back with the sealed private keys, so the account can open nothing.
    """
    return AccountIdentity.objects.filter(
        user=user, state=AccountIdentity.State.ACTIVE
    ).exists()


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def index(request):
    if not _has_active_identity(request.user):
        return redirect("vault_ui:onboarding")
    return render(request, "vault/ui/index.html")


@csp(settings.VAULT_CSP)
@login_required
@ensure_csrf_cookie
def onboarding(request):
    # Walking it twice would mint a new salt, and the sealed private keys are
    # the only path back to every VaultKeyWrap the account holds.
    if _has_active_identity(request.user):
        return redirect("vault_ui:index")
    return render(request, "vault/ui/onboarding.html")
