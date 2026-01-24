from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..models import File


@login_required
def index(request):
    root_nodes = (
        File.objects.filter(owner=request.user, parent__isnull=True)
        .order_by('node_type', 'name')
    )
    context = {
        'root_nodes': root_nodes,
    }
    return render(request, 'files/ui/index.html', context)
