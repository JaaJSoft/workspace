#!/usr/bin/env python3
"""Fail when a runtime dependency carries a license the project cannot ship.

The project is BUSL 1.1 and converts to MIT, so a GPL, AGPL or non-commercial
dependency cannot be part of it; the policy and its reasons are in CLAUDE.md
("Dependency licenses"). This checks what is distributed: the Python packages
of ``uv.lock`` without the dev group, and the non-dev entries of the frontend
lockfile.

    uv sync --frozen --no-dev
    python scripts/check_licenses.py

Run it in an environment synced with the dev group left out, or the wheels it
cannot find are reported as unchecked. A package whose metadata names no
usable license goes in ``REVIEWED`` with the reason it is fine, so a new one
is a decision somebody made and not a gap.
"""

import json
import os
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_LOCK = ROOT / "scripts" / "frontend" / "package-lock.json"

# Permissive licenses. LGPL and MPL (matched by prefix, below) are the weak
# copyleft ones CLAUDE.md allows under conditions a lockfile cannot check.
ALLOWED = frozenset(
    {
        "mit",
        "mit-0",
        "bsd",
        "bsd-2-clause",
        "bsd-3-clause",
        "0bsd",
        "isc",
        "apache-2.0",
        "python-2.0",
        "psf-2.0",
        "zpl-2.1",
        "zlib",
        "hpnd",
        "cc0-1.0",
        "unlicense",
        "blueoak-1.0.0",
        "bsl-1.0",
        "unicode-3.0",
        "unicode-dfs-2016",
        "mit-cmu",
        "cnri-python",
    }
)
WEAK_COPYLEFT_PREFIXES = ("lgpl-", "mpl-2.0")

# What the Trove classifiers and free-text License fields of older packages
# call the licenses above. Replaced as whole phrases before the expression is
# split, longest first.
PHRASES = {
    "3-clause bsd license": "bsd-3-clause",
    "bsd 3-clause": "bsd-3-clause",
    "mit license": "mit",
    "bsd license": "bsd",
    "apache software license": "apache-2.0",
    "apache license 2.0": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "python software foundation license": "psf-2.0",
    "isc license (iscl)": "isc",
    "zope public license": "zpl-2.1",
    "historical permission notice and disclaimer (hpnd)": "hpnd",
    "public domain": "unlicense",
    "the unlicense (unlicense)": "unlicense",
    "mozilla public license 2.0 (mpl 2.0)": "mpl-2.0",
    "gnu lesser general public license v3 (lgplv3)": "lgpl-3.0",
    "gnu lesser general public license v3 or later (lgplv3+)": "lgpl-3.0-or-later",
    "gnu library or lesser general public license (lgpl)": "lgpl-2.1",
}
_PHRASE = re.compile(
    r"(?<![\w-])("
    + "|".join(map(re.escape, sorted(PHRASES, key=len, reverse=True)))
    + r")(?![\w-])",
    re.IGNORECASE,
)

# (ecosystem, name) -> (the license it declared when reviewed, None for none,
# why it is fine).
# A package declaring anything else is checked again: the review was of that
# license, not of the name.
REVIEWED = {
    ("pypi", "py-rust-stemmers"): (
        None,
        "MIT: its LICENSE file; the metadata is empty",
    ),
    ("pypi", "sqlite-vec"): (
        "MIT License, Apache License, Version 2.0",
        "MIT or Apache-2.0: dual-licensed, said in free text",
    ),
    ("npm", "rgbcolor"): (
        "MIT OR SEE LICENSE IN FEEL-FREE.md",
        "MIT, or a free-use grant",
    ),
}

# Wheels that bundle native libraries ship a notice listing them, and the
# declared license is then only the Python half: pillow-heif is BSD and carries
# libx265, GPL-2.0. package -> why the strong copyleft it names is fine.
BUNDLED_NOTICE_REVIEWED = {}

_NOTICE_FILE = re.compile(r"bundled|third[-_ ]?party|notice", re.IGNORECASE)
# GPL and AGPL, not LGPL; a line granting a linking exception is a runtime.
# The spelled-out name only counts on a "License:" field line, the form of a
# bundled-libraries listing: notices also carry whole license texts, which
# name the GPL in passing (MPL-2.0 defines its "Secondary Licenses" with it).
_STRONG_COPYLEFT = re.compile(
    r"(?<![A-Za-z])A?GPL"
    r"|^\s*licen[cs]e\s*:.*(?<!lesser )(?<!library )general public license",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"\(|\)|[^\s()]+")


def _atom(tokens):
    token = tokens.pop(0)
    if token == ")":
        raise ValueError("unbalanced parenthesis")
    if token == "(":
        verdict = _or(tokens)
        if not tokens or tokens.pop(0) != ")":
            raise ValueError("unbalanced parenthesis")
        return verdict
    if tokens and tokens[0].upper() == "WITH":
        # The exception does not make a GPL license a permissive one.
        del tokens[:2]
    return _is_allowed(token)


def _and(tokens):
    verdict = _atom(tokens)
    while tokens and tokens[0].upper() == "AND":
        tokens.pop(0)
        verdict = _atom(tokens) and verdict
    return verdict


def _or(tokens):
    verdict = _and(tokens)
    while tokens and tokens[0].upper() == "OR":
        tokens.pop(0)
        verdict = _and(tokens) or verdict
    return verdict


def _is_allowed(token):
    token = token.lower()
    return token in ALLOWED or token.startswith(WEAK_COPYLEFT_PREFIXES)


def evaluate(expression):
    """Whether a license expression can be shipped.

    ``OR`` needs one allowed side, ``AND`` every side. Text that is not an
    expression (a sentence, a file name) leaves tokens over and fails.
    """
    expression = _PHRASE.sub(lambda m: PHRASES[m.group(1).lower()], expression.strip())
    tokens = _TOKEN.findall(expression)
    if not tokens:
        return False
    try:
        verdict = _or(tokens)
    except IndexError, ValueError:
        return False
    return verdict and not tokens


def python_license(distribution):
    """``(allowed, what the metadata says)`` for an installed distribution;
    what it says is None when it names no license at all."""
    meta = distribution.metadata
    expression = meta.get("License-Expression")
    if expression:
        return evaluate(expression), expression
    classifiers = [
        classifier.split("::")[-1].strip()
        for classifier in meta.get_all("Classifier") or []
        if classifier.startswith("License ::")
    ]
    classifiers = [c for c in classifiers if c != "OSI Approved"]
    if classifiers:
        # Several classifiers are the choices of a dual-licensed package.
        return any(evaluate(c) for c in classifiers), "; ".join(classifiers)
    text = (meta.get("License") or "").strip()
    if not text:
        # None, not a label: no license text can then pass for its absence.
        return False, None
    if "\n" not in text and len(text) <= 120:
        return evaluate(text), text
    # Kept apart from the empty case: a REVIEWED entry for a package with no
    # metadata must not cover the license text it ships later.
    return False, f"unusable license text: {text.splitlines()[0][:80]}"


def python_runtime_names():
    """The names ``uv.lock`` ships, dev group left out."""
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout
    return sorted(
        {
            m.group(1)
            for line in exported.splitlines()
            if (m := re.match(r"([\w.-]+)==", line))
        }
    )


def bundled_copyleft(distribution):
    """Lines of the notices a wheel ships that name a GPL or AGPL library."""
    lines = []
    for file in distribution.files or []:
        # Wherever the wheel put it: dist-info/licenses/, <package>.libs/...
        if not _NOTICE_FILE.search(file.name):
            continue
        for line in (
            file.locate().read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            if _STRONG_COPYLEFT.search(line) and "exception" not in line.lower():
                lines.append(line.strip())
    return lines


def check_python():
    problems, unchecked = [], []
    names = python_runtime_names()
    for name in names:
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            unchecked.append(name)
            continue
        allowed, declared = python_license(distribution)
        label = f"{name} {distribution.version}"
        if not allowed and not is_reviewed("pypi", name, declared):
            problems.append(f"{label}: {declared or 'no license metadata'}")
        if name not in BUNDLED_NOTICE_REVIEWED:
            for line in bundled_copyleft(distribution):
                problems.append(f"{label} bundles strong copyleft code: {line}")
    return len(names), problems, unchecked


def is_reviewed(ecosystem, name, declared):
    """Whether a package was reviewed with the license it declares now."""
    review = REVIEWED.get((ecosystem, name))
    return review is not None and review[0] == declared


def _lock_license(entry):
    license_ = entry.get("license")
    if isinstance(license_, dict):
        return license_.get("type") or ""
    if isinstance(license_, list):
        return " OR ".join(item.get("type", "") for item in license_)
    return license_ or ""


def check_npm():
    problems, count = [], 0
    for path, entry in json.loads(FRONTEND_LOCK.read_text(encoding="utf-8"))[
        "packages"
    ].items():
        if not path or entry.get("dev"):
            continue
        count += 1
        name = path.rsplit("node_modules/", 1)[-1]
        declared = _lock_license(entry)
        if not evaluate(declared) and not is_reviewed("npm", name, declared):
            problems.append(
                f"{name} {entry.get('version', '')}: {declared or 'no license'}"
            )
    return count, problems


def _annotate(level, title, message):
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level} title={title}::{message}")
    else:
        print(f"{level.upper()}: {title}: {message}")


def main():
    python_count, python_problems, unchecked = check_python()
    npm_count, npm_problems = check_npm()
    print(
        f"Python: {python_count} runtime packages. npm: {npm_count} bundled packages."
    )
    if unchecked:
        _annotate(
            "notice",
            "Licenses not checked",
            f"not installed on this platform, so not checked: {', '.join(unchecked)}",
        )
    for problem in python_problems:
        _annotate("error", "Python license", problem)
    for problem in npm_problems:
        _annotate("error", "npm license", problem)
    if python_problems or npm_problems:
        print("See 'Dependency licenses' in CLAUDE.md for what is allowed.")
        return 1
    print("Every runtime dependency carries an allowed license.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
