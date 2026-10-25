"""Extract and compile the gettext catalogs of the workspace modules."""

import contextlib
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.commands import makemessages

from workspace.core.services.translations import shared_catalogs

# Paths no catalog reads strings from: test fixtures, migrations (copies of
# the model strings), and vendored bundles, whose own gettext calls would
# land in our djangojs catalog.
_DJANGO_IGNORE = ["tests", "migrations", "static", "locale"]
_DJANGOJS_IGNORE = ["tests", "vendor", "locale"]

# msgmerge stamps the extraction time in every catalog it touches, even when
# no message changed.
_POT_DATE_RE = re.compile(r'^"POT-Creation-Date: .*\\n"$', re.MULTILINE)

# The placeholders xgettext writes in a new catalog's header. The fuzzy flag
# goes with them: msgfmt drops a fuzzy header, Plural-Forms included.
_HEADER_PLACEHOLDER_RE = re.compile(
    r'^(#.*\n)+|^"(Report-Msgid-Bugs-To|PO-Revision-Date|Last-Translator|Language-Team): .*\\n"\n',
    re.MULTILINE,
)


def workspace_modules():
    """Module name -> directory, for every top-level app under workspace/."""
    root = Path(settings.BASE_DIR) / "workspace"
    return {
        Path(app.path).name: Path(app.path)
        for app in apps.get_app_configs()
        if Path(app.path).parent == root
    }


def translated_languages():
    """Every language but the source one: msgids are already in LANGUAGE_CODE."""
    return [
        code for code, _name in settings.LANGUAGES if code != settings.LANGUAGE_CODE
    ]


def tidy_new_header(text, language):
    """Fill in a catalog makemessages just created: its header is a template."""
    header, sep, messages = text.partition("\n\n")
    header = _HEADER_PLACEHOLDER_RE.sub("", header)
    header = header.replace(
        '"Project-Id-Version: PACKAGE VERSION\\n"', '"Project-Id-Version: workspace\\n"'
    )
    header = header.replace('"Language: \\n"', f'"Language: {language}\\n"')
    return header + sep + messages


class _MakeMessages(makemessages.Command):
    """makemessages, minus the msgids the given catalogs already hold."""

    def __init__(self, exclude_catalogs):
        super().__init__()
        self.xgettext_options = [
            *self.xgettext_options,
            *(f"--exclude-file={catalog}" for catalog in exclude_catalogs),
        ]


class Command(BaseCommand):
    requires_system_checks = []
    help = (
        "Extract the translatable strings of the given modules into their "
        "locale/<lang>/LC_MESSAGES/django.po and djangojs.po, then compile "
        "every catalog of those modules. Without a module name, runs on every "
        "module that already has a locale/ directory."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "modules",
            nargs="*",
            help="Module names (users, files, ...). Naming one starts its catalog.",
        )
        parser.add_argument(
            "--compile-only",
            action="store_true",
            help="Compile the existing .po files without extracting.",
        )

    def handle(self, *args, modules, compile_only, **options):
        available = workspace_modules()
        unknown = sorted(set(modules) - set(available))
        if unknown:
            raise CommandError(f"Unknown module(s): {', '.join(unknown)}")
        if modules:
            targets = [available[name] for name in modules]
        else:
            targets = [
                path
                for _name, path in sorted(available.items())
                if (path / "locale").is_dir()
            ]
        languages = translated_languages()
        for module_dir in targets:
            (module_dir / "locale").mkdir(exist_ok=True)
            with contextlib.chdir(module_dir):
                if not compile_only:
                    self._extract(module_dir, languages)
                call_command("compilemessages", locale=languages, verbosity=0)
            self.stdout.write(f"{module_dir.name}: done")

    def _extract(self, module_dir, languages):
        before = {
            po: po.read_text(encoding="utf-8")
            for po in module_dir.glob("locale/*/LC_MESSAGES/*.po")
        }
        makemessages_options = {
            "locale": languages,
            "no_obsolete": True,
            "add_location": "file",
            "verbosity": 0,
        }
        for domain, ignore_patterns in (
            ("django", _DJANGO_IGNORE),
            ("djangojs", _DJANGOJS_IGNORE),
        ):
            call_command(
                _MakeMessages(shared_catalogs(module_dir, domain)),
                domain=domain,
                ignore_patterns=ignore_patterns,
                **makemessages_options,
            )
        for po in module_dir.glob("locale/*/LC_MESSAGES/*.po"):
            new = po.read_text(encoding="utf-8")
            old = before.get(po)
            if old is None:
                language = po.parent.parent.name
                po.write_text(tidy_new_header(new, language), encoding="utf-8")
            elif _POT_DATE_RE.sub("", new) == _POT_DATE_RE.sub("", old):
                po.write_text(old, encoding="utf-8")
