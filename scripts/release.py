#!/usr/bin/env python3
"""Release script for Workspace.

Usage:
    python scripts/release.py <version>

Example:
    python scripts/release.py 0.2.0

This script will:
    1. Verify the CHANGELOG.md contains an entry for the version
    2. Verify the git working directory is clean
    3. Update the version in pyproject.toml via `uv version`
    4. Update the LICENSE file (Licensed Work version + Change Date)
    5. Update the README.md (MIT conversion date)
    6. Commit all changes
    7. Tag the commit with v<version>
"""

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

BSL_YEARS = 3

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, **kwargs)
    if result.returncode != 0:
        print(f"  FAILED: {' '.join(cmd)}")
        if result.stderr:
            print(result.stderr.strip())
        sys.exit(1)
    return result


def validate_version(version: str) -> None:
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        print(f"Error: invalid version format '{version}'. Expected X.Y.Z")
        sys.exit(1)


def check_changelog(version: str) -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {version}" not in changelog:
        print(f"Error: CHANGELOG.md has no entry for version {version}")
        print(f"Add a '## {version}' section before releasing.")
        sys.exit(1)
    print(f"  CHANGELOG.md contains {version}")


def check_git_clean() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.stdout.strip():
        print("Error: git working directory is not clean.")
        print("Commit or stash your changes before releasing.")
        print(result.stdout)
        sys.exit(1)
    print("  Git working directory is clean")


def compute_change_date(today: date) -> date:
    try:
        return date(today.year + BSL_YEARS, today.month, today.day)
    except ValueError:
        # Feb 29 on a non-leap year target
        return date(today.year + BSL_YEARS, today.month, today.day - 1)


def update_pyproject(version: str) -> None:
    run(["uv", "version", version])
    print(f"  pyproject.toml -> {version}")


def update_license(version: str, change_date: str) -> None:
    path = ROOT / "LICENSE"
    content = path.read_text(encoding="utf-8")

    content = re.sub(
        r"(Licensed Work:\s+JaaJSoft Workspace version )\S+",
        rf"\g<1>{version}.",
        content,
    )
    content = re.sub(
        r"(Change Date:\s+)\S+",
        rf"\1{change_date}",
        content,
    )

    path.write_text(content, encoding="utf-8")
    print(f"  LICENSE -> version {version}, change date {change_date}")


def update_readme(change_date: str) -> None:
    path = ROOT / "README.md"
    content = path.read_text(encoding="utf-8")

    content = re.sub(
        r"(under the MIT License on )\d{4}-\d{2}-\d{2}",
        rf"\g<1>{change_date}",
        content,
    )

    path.write_text(content, encoding="utf-8")
    print(f"  README.md -> MIT date {change_date}")


def git_commit_and_tag(version: str) -> None:
    tag = f"v{version}"

    files = ["pyproject.toml", "LICENSE", "README.md"]

    # Include uv.lock if it was modified
    result = subprocess.run(
        ["git", "diff", "--name-only", "uv.lock"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if "uv.lock" in result.stdout:
        files.append("uv.lock")

    run(["git", "add", *files])
    run(["git", "commit", "-m", f"release: {tag}"])
    run(["git", "tag", "-a", tag, "-m", f"Release {tag}"])
    print(f"  Committed and tagged {tag}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/release.py <version>")
        print("Example: python scripts/release.py 0.2.0")
        sys.exit(1)

    version = sys.argv[1].lstrip("v")

    validate_version(version)

    change_date = compute_change_date(date.today()).isoformat()

    print(f"Releasing Workspace v{version}")
    print(f"  BSL change date: {change_date}")
    print()

    print("Preflight checks...")
    check_changelog(version)
    check_git_clean()
    print()

    print("Updating files...")
    update_pyproject(version)
    update_license(version, change_date)
    update_readme(change_date)
    print()

    print("Committing...")
    git_commit_and_tag(version)
    print()

    print(f"Done! Released v{version}")
    print(f"Push with: git push && git push --tags")


if __name__ == "__main__":
    main()
