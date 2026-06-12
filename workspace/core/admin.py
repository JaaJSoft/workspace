from django import forms
from django.contrib import admin

from workspace.core.models import ModuleAccessRule
from workspace.core.services.module_access import restrictable_module_slugs


class ModuleAccessRuleForm(forms.ModelForm):
    class Meta:
        model = ModuleAccessRule
        fields = ["module_slug", "group", "is_enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(slug, slug) for slug in sorted(restrictable_module_slugs())]
        self.fields["module_slug"] = forms.ChoiceField(choices=choices)


@admin.register(ModuleAccessRule)
class ModuleAccessRuleAdmin(admin.ModelAdmin):
    form = ModuleAccessRuleForm
    list_display = ("module_slug", "group", "is_enabled", "updated_at")
    list_filter = ("module_slug", "is_enabled", "group")
    search_fields = ("module_slug",)
