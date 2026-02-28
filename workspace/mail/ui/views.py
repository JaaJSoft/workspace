import orjson

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.mail.models import MailAccount
from workspace.mail.serializers import MailAccountSerializer
from workspace.mail.services.oauth2 import get_available_providers


@login_required
@ensure_csrf_cookie
def index(request):
    accounts = MailAccount.objects.filter(owner=request.user, is_active=True)

    return render(request, 'mail/ui/index.html', {
        'accounts': accounts,
        'accounts_json': orjson.dumps(
            MailAccountSerializer(accounts, many=True).data,
        ).decode(),
        'oauth_providers_json': orjson.dumps(
            get_available_providers(),
        ).decode(),
    })
