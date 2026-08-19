from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.core"

    def ready(self):
        _patch_django_daisy_result_list()


def _patch_django_daisy_result_list():
    """Re-register django-daisy's ``{% daisy_result_list %}`` tag for Django 6.1.

    Django 6.1 added a leading ``name`` argument to ``InclusionAdminNode``;
    django-daisy (up to 2.0.11 at least) still calls the old signature, so every
    admin change list raises ``TypeError: ... missing 1 required positional
    argument: 'token'``. Overriding the tag in the library's own registry is
    enough: ``{% load dash_tags %}`` resolves to that registry. Drop this once
    django-daisy ships a release built against Django 6.1.
    """
    from django.contrib.admin.templatetags.base import InclusionAdminNode
    from django_daisy.templatetags import dash_tags

    @dash_tags.register.tag(name="daisy_result_list")
    def daisy_result_list_tag(parser, token):
        return InclusionAdminNode(
            "daisy_result_list",
            parser,
            token,
            func=dash_tags.daisy_result_list,
            template_name="change_list_results.html",
            takes_context=False,
        )
