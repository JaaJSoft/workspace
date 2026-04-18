from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from workspace.core.changelog import get_changelog_entries


@login_required
def changelog_partial(request):
    """Return rendered changelog HTML partial for the modal."""
    entries = get_changelog_entries()
    return render(request, 'core/partials/changelog.html', {
        'entries': entries,
    })
