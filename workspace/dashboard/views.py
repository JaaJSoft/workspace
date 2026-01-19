from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def index(request):
    """Dashboard home page"""
    return render(request, 'dashboard/index.html')
