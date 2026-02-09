from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404
from django.shortcuts import render

from workspace.users import avatar_service


@login_required
def profile_view(request, username=None):
    if username is None:
        profile_user = request.user
    else:
        try:
            profile_user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise Http404
    return render(request, 'users/ui/profile.html', {
        'profile_user': profile_user,
        'is_own_profile': profile_user == request.user,
    })


@login_required
def settings_view(request):
    return render(request, 'users/ui/settings.html', {
        'has_avatar': avatar_service.has_avatar(request.user),
    })


@login_required
def user_card_view(request, user_id):
    try:
        card_user = User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        raise Http404
    return render(request, 'users/ui/partials/user_card.html', {'card_user': card_user})
