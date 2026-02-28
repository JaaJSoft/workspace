"""OAuth2 views for mail account connection."""

import logging

import orjson
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.mail.models import MailAccount
from workspace.mail.serializers import MailAccountSerializer
from workspace.mail.services.oauth2 import (
    build_authorize_url,
    exchange_code,
    fetch_userinfo,
    get_available_providers,
    get_provider_config,
)

logger = logging.getLogger(__name__)


class OAuthProvidersView(APIView):
    """List OAuth2 providers that are configured and available."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_available_providers())


class OAuthAuthorizeView(APIView):
    """Start OAuth2 flow: redirect to provider's authorization page."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        provider = request.query_params.get('provider', '')
        config = get_provider_config(provider)
        if not config:
            return HttpResponseBadRequest('Unknown provider')

        callback_url = request.build_absolute_uri(
            reverse('mail_ui:mail-oauth2-callback'),
        )
        authorize_url, state = build_authorize_url(provider, callback_url)

        # Store state and provider in session for validation on callback
        request.session['oauth2_state'] = state
        request.session['oauth2_provider'] = provider

        return HttpResponseRedirect(authorize_url)


def oauth2_callback(request):
    """Handle OAuth2 callback: exchange code, create account, postMessage."""
    error = request.GET.get('error')
    if error:
        return _render_callback(request, {
            'type': 'oauth2-error',
            'error': request.GET.get('error_description', error),
        })

    code = request.GET.get('code', '')
    state = request.GET.get('state', '')

    # Validate state
    session_state = request.session.pop('oauth2_state', '')
    provider = request.session.pop('oauth2_provider', '')

    if not state or state != session_state:
        return _render_callback(request, {
            'type': 'oauth2-error',
            'error': 'Invalid state parameter. Please try again.',
        })

    if not provider:
        return _render_callback(request, {
            'type': 'oauth2-error',
            'error': 'Missing provider. Please try again.',
        })

    try:
        callback_url = request.build_absolute_uri(
            reverse('mail_ui:mail-oauth2-callback'),
        )
        tokens = exchange_code(provider, code, callback_url)
    except Exception:
        logger.exception("OAuth2 code exchange failed for provider '%s'", provider)
        return _render_callback(request, {
            'type': 'oauth2-error',
            'error': 'Failed to exchange authorization code. Please try again.',
        })

    # Fetch user email from provider
    userinfo = fetch_userinfo(provider, tokens['access_token'])
    email = userinfo.get('email', '') if userinfo else ''

    if not email:
        return _render_callback(request, {
            'type': 'oauth2-error',
            'error': 'Could not determine email address from provider.',
        })

    # Create the mail account
    config = get_provider_config(provider)
    account = MailAccount(
        owner=request.user,
        email=email,
        username=email,
        display_name='',
        auth_method='oauth2',
        oauth2_provider=provider,
        imap_host=config['imap_host'],
        imap_port=config['imap_port'],
        imap_use_ssl=config['imap_use_ssl'],
        smtp_host=config['smtp_host'],
        smtp_port=config['smtp_port'],
        smtp_use_tls=config['smtp_use_tls'],
    )
    account.set_oauth2_data(tokens)
    account.save()

    account_data = MailAccountSerializer(account).data

    return _render_callback(request, {
        'type': 'oauth2-success',
        'account': account_data,
    })


def _render_callback(request, result):
    """Render the callback page with postMessage."""
    return render(request, 'mail/oauth2_callback.html', {
        'result_json': orjson.dumps(result).decode(),
    })
