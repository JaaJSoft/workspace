from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import FaceAnalysis, PhotoEmbedding


@admin.register(FaceAnalysis)
class FaceAnalysisAdmin(ModelAdmin):
    """Which photos were read for faces, and by which backend.

    Read-only, and deliberately the only face model here: faces and clusters
    are biometric data an administrator has no reason to browse.
    """

    list_display = ["file", "owner", "backend", "version", "face_count", "analyzed_at"]
    list_filter = ["backend", "version"]
    search_fields = ["file__name", "owner__username"]
    list_select_related = ["file", "owner"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PhotoEmbedding)
class PhotoEmbeddingAdmin(ModelAdmin):
    """Which photos were embedded for similarity and search, and by which
    model. Read-only; the vectors themselves are not shown."""

    list_display = ["file", "owner", "model", "has_vector", "analyzed_at"]
    list_filter = ["model"]
    search_fields = ["file__name", "owner__username"]
    list_select_related = ["file", "owner"]
    exclude = ["embedding"]

    @admin.display(boolean=True, description="Vector")
    def has_vector(self, obj):
        return obj.embedding is not None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
