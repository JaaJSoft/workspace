from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.mail.models import MailAccount, MailFolder, MailLabel
from workspace.mail.serializers import (
    MailAccountSerializer, MailFolderSerializer, MailLabelSerializer,
)
from workspace.mail.services.oauth2 import get_available_providers


@login_required
@ensure_csrf_cookie
def index(request):
    accounts = MailAccount.objects.filter(owner=request.user, is_active=True)
    labels = MailLabel.objects.filter(account__in=accounts)
    folders = MailFolder.objects.filter(account__in=accounts)

    return render(request, 'mail/ui/index.html', {
        'accounts': MailAccountSerializer(accounts, many=True).data,
        'labels': MailLabelSerializer(labels, many=True).data,
        'folders': MailFolderSerializer(folders, many=True).data,
        'oauth_providers': get_available_providers(),
    })
