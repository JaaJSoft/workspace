import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.mail.models import MailAccount
from workspace.mail.serializers import MailAccountSerializer


@login_required
@ensure_csrf_cookie
def index(request):
    accounts = MailAccount.objects.filter(owner=request.user, is_active=True)

    return render(request, 'mail/ui/index.html', {
        'accounts': accounts,
        'accounts_json': json.dumps(
            MailAccountSerializer(accounts, many=True).data,
        ),
    })
