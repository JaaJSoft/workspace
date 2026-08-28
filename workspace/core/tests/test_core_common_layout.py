"""Guards the ``core`` / ``common`` split.

``common`` is a leaf: a toolbox that never names another app, so any module
can import it without creating a cycle. ``core`` is the root: the app itself,
free to import from every module. The rule is documented in CLAUDE.md
("core vs common"); this test makes the leaf half of it mechanical.
"""

import ast
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase

import workspace.common

COMMON_DIR = Path(workspace.common.__file__).parent


def _workspace_apps():
    return {
        app.split(".")[1]
        for app in settings.INSTALLED_APPS
        if app.startswith("workspace.")
    }


def _other_apps():
    return _workspace_apps() - {"common"}


def _source_files(suffix):
    for path in sorted(COMMON_DIR.rglob(f"*{suffix}")):
        parts = path.relative_to(COMMON_DIR).parts
        if "tests" in parts or "vendor" in parts:
            continue
        yield path


# Only block comments may span lines: `{# #}` is single-line by Django's lexer
# and `//` by definition, so the `.` of DOTALL must not reach them.
_TEMPLATE_COMMENTS = re.compile(
    r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}|\{#[^\n]*?#\}|<!--.*?-->",
    re.DOTALL,
)
_JS_COMMENTS = re.compile(r"/\*.*?\*/|^[ \t]*//[^\n]*", re.DOTALL | re.MULTILINE)


def _strip_comments(path, text):
    if path.suffix == ".html":
        return _TEMPLATE_COMMENTS.sub("", text)
    if path.suffix == ".js":
        return _JS_COMMENTS.sub("", text)
    return text


def _text_offences(path):
    """Ways a template or script names another app."""
    apps_alt = "|".join(sorted(_other_apps()))
    text = _strip_comments(path, path.read_text(encoding="utf-8"))
    offences = []
    for match in re.finditer(r"\{%\s*url\s+['\"]([a-z_]+):", text):
        if match.group(1).split("_")[0] in _other_apps():
            offences.append(f"url tag {match.group(1)}:")
    for match in re.finditer(r"\{%\s*(?:include|extends)\s+['\"]([a-z_]+)/", text):
        if match.group(1) in _other_apps():
            offences.append(f"template path {match.group(1)}/")
    if re.search(r"/api/v1/", text):
        offences.append("hard-coded /api/v1/ endpoint")
    for match in re.finditer(rf"[\"'`]/({apps_alt})(?:/|[\"'`?#])", text):
        offences.append(f"hard-coded /{match.group(1)}/ path")
    return offences


def _inside_common(dotted):
    return dotted == "workspace.common" or dotted.startswith("workspace.common.")


def _package_of(path):
    """Dotted package a relative import in *path* resolves against."""
    parts = ["workspace", "common", *path.relative_to(COMMON_DIR).with_suffix("").parts]
    parts.pop()  # the module itself, or "__init__" for a package
    return parts


def _import_offences(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offences = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        elif isinstance(node, ast.ImportFrom):
            # `from ..x import y`: one dot is the current package, each
            # extra dot climbs one level - enough of them escape common.
            base = _package_of(path)
            base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
            names = [".".join([*base, node.module] if node.module else base)]
        else:
            continue
        for name in names:
            if (
                name == "workspace" or name.startswith("workspace.")
            ) and not _inside_common(name):
                offences.append(f"imports {name}")
    return offences


def _deviations():
    found = {}
    for path in _source_files(".py"):
        if offences := _import_offences(path):
            found[path.relative_to(COMMON_DIR).as_posix()] = offences
    for suffix in (".html", ".js"):
        for path in _source_files(suffix):
            if offences := _text_offences(path):
                found[path.relative_to(COMMON_DIR).as_posix()] = offences
    return found


class CommonIsALeafTests(SimpleTestCase):
    def test_common_has_no_app_surface(self):
        # A leaf has nothing to route, persist or schedule - those belong to
        # core or to the module that owns the concept.
        self.assertEqual(list(apps.get_app_config("common").get_models()), [])
        for filename in ("urls.py", "tasks.py"):
            self.assertFalse((COMMON_DIR / filename).exists(), filename)

    def test_common_never_names_another_app(self):
        self.assertEqual(
            _deviations(),
            {},
            "workspace/common must not know about other apps - move the file "
            "to the module it names, or make the reference a parameter "
            "(see 'core vs common' in CLAUDE.md)",
        )
