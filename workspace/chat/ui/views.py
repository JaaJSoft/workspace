from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@login_required
@ensure_csrf_cookie
def chat_view(request):
    """Main chat page."""
    return render(request, 'chat/ui/index.html', {
        'navbar_full_width': True,
    })
