"""Build the Synology package (.spk) of Workspace.

The package is a Container Manager project (DSM 7.2.1 and later): it ships no
binary, only the compose file that pulls the multi-architecture image the
Docker workflow publishes, so one ``noarch`` package covers every x86-64 and
arm64 NAS.

An .spk is an uncompressed tar holding INFO, conf/, scripts/, the wizard
files, the icons and package.tgz, an xz-compressed tar of what DSM extracts
into /var/packages/<package>/target. This script writes that layout with the
standard library, the way the Synology toolkit's ``pkg_make_spk`` does,
without the toolkit's DSM chroot.

    uv run --no-project --with pillow python packaging/synology/build.py \\
        --version 0.43.0 --image ghcr.io/jaajsoft/workspace:0.43.0
"""

import argparse
import hashlib
import io
import math
import re
import tarfile
import time
from pathlib import Path

from PIL import Image

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
APP_ICON = REPO_ROOT / "workspace/core/static/icons/icon-512.png"

PACKAGE = "workspace"
# Host port the web container is published on. It ends up in the compose
# file, the DSM main-menu shortcut, the Package Center "Open" button and the
# wizard text.
PORT = 8765
REDIS_IMAGE = "redis:7.4-alpine"
# The docker-project worker ships with Container Manager 20.10.23-1432, the
# first one on DSM 7.2.1.
OS_MIN_VER = "7.2.1-69057"
CONTAINER_MANAGER = "ContainerManager>=20.10.23-1432"

INFO = {
    "package": PACKAGE,
    "displayname": "Workspace",
    "description": (
        "Self-hosted workspace: files, notes, mail, calendar, chat, projects, "
        "photos and contacts in one place."
    ),
    "description_fre": (
        "Espace de travail auto-hébergé : fichiers, notes, mail, agenda, chat, "
        "projets, photos et contacts au même endroit."
    ),
    "maintainer": "JaaJSoft",
    "maintainer_url": "https://github.com/JaaJSoft/workspace",
    "helpurl": "https://github.com/JaaJSoft/workspace/tree/main/docs/deployments/synology",
    "arch": "noarch",
    "os_min_ver": OS_MIN_VER,
    "install_dep_packages": CONTAINER_MANAGER,
    "thirdparty": "yes",
    "dsmuidir": "ui",
    "dsmappname": "SYNO.SDS.workspace.Application",
    "adminprotocol": "http",
    "adminport": str(PORT),
    "adminurl": "/",
}

# Files in which the build replaces a placeholder with its value.
TEMPLATED = {"compose.yaml", "config", "install_uifile", "install_uifile_fre"}
EXECUTABLE_DIRS = {"scripts"}
EXECUTABLE_FILES = {"synology.sh"}
UI_ICON_SIZES = (16, 24, 32, 48, 64, 72, 256)

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def substitutions(image):
    return {
        "__IMAGE__": image,
        "__REDIS_IMAGE__": REDIS_IMAGE,
        "__PORT__": str(PORT),
    }


def render(path, values):
    data = path.read_bytes()
    if path.name not in TEMPLATED:
        return data
    text = data.decode()
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    leftover = re.findall(r"__[A-Z_]+__", text)
    if leftover:
        raise SystemExit(f"{path}: unreplaced placeholders {sorted(set(leftover))}")
    return text.encode()


def png(size):
    with Image.open(APP_ICON) as icon:
        resized = icon.convert("RGBA").resize((size, size), Image.LANCZOS)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


class Archive:
    """A tar written in a fixed order with fixed metadata, so that two builds
    of the same sources produce the same bytes."""

    def __init__(self, fileobj, mode, mtime):
        self.tar = tarfile.open(fileobj=fileobj, mode=mode, format=tarfile.GNU_FORMAT)
        self.mtime = mtime
        self.dirs = set()

    def _info(self, name, kind, mode, size=0):
        info = tarfile.TarInfo(name)
        info.type = kind
        info.mode = mode
        info.size = size
        info.mtime = self.mtime
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        return info

    def _parents(self, name):
        parts = name.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            directory = "/".join(parts[:depth])
            if directory not in self.dirs:
                self.dirs.add(directory)
                self.tar.addfile(self._info(directory, tarfile.DIRTYPE, 0o755))

    def add(self, name, data, executable=False):
        self._parents(name)
        info = self._info(
            name, tarfile.REGTYPE, 0o755 if executable else 0o644, len(data)
        )
        self.tar.addfile(info, io.BytesIO(data))

    def close(self):
        self.tar.close()


def tree(root):
    return sorted(p for p in root.rglob("*") if p.is_file())


def build_target(values, mtime):
    """package.tgz: what DSM extracts into the package's target directory."""
    root = PACKAGE_DIR / "target"
    files = {}
    for path in tree(root):
        files[path.relative_to(root).as_posix()] = (
            render(path, values),
            path.name in EXECUTABLE_FILES,
        )
    for size in UI_ICON_SIZES:
        files[f"ui/images/{PACKAGE}_{size}.png"] = (png(size), False)

    buffer = io.BytesIO()
    archive = Archive(buffer, "w:xz", mtime)
    for name in sorted(files):
        data, executable = files[name]
        archive.add(name, data, executable)
    archive.close()
    extract_kib = math.ceil(sum(len(data) for data, _ in files.values()) / 1024)
    return buffer.getvalue(), extract_kib


def build_info(version, package_tgz, extract_kib, mtime):
    info = dict(INFO)
    info["version"] = version
    info["checksum"] = hashlib.md5(package_tgz).hexdigest()
    info["extractsize"] = str(extract_kib)
    info["create_time"] = time.strftime("%Y%m%d-%H:%M:%S", time.gmtime(mtime))
    for key, value in info.items():
        if '"' in value or "\n" in value:
            raise SystemExit(f"INFO {key}: a value may not hold a quote or a newline")
    return "".join(f'{key}="{value}"\n' for key, value in info.items()).encode()


def build_spk(version, image, output, mtime):
    values = substitutions(image)
    package_tgz, extract_kib = build_target(values, mtime)
    members = {
        "INFO": (build_info(version, package_tgz, extract_kib, mtime), False),
        "package.tgz": (package_tgz, False),
        "PACKAGE_ICON.PNG": (png(64), False),
        "PACKAGE_ICON_256.PNG": (png(256), False),
    }
    for directory in ("conf", "scripts", "WIZARD_UIFILES"):
        for path in tree(PACKAGE_DIR / directory):
            name = path.relative_to(PACKAGE_DIR).as_posix()
            members[name] = (render(path, values), directory in EXECUTABLE_DIRS)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fileobj:
        archive = Archive(fileobj, "w", mtime)
        for name in sorted(members):
            data, executable = members[name]
            archive.add(name, data, executable)
        archive.close()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True, help="Workspace version, X.Y.Z")
    parser.add_argument(
        "--build",
        type=int,
        default=1,
        help="Package build number, raised to republish the same version (default 1)",
    )
    parser.add_argument(
        "--image", required=True, help="Workspace image the package runs"
    )
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument(
        "--mtime",
        type=int,
        default=None,
        help="Timestamp of every archive member (default: now; CI passes the commit time)",
    )
    args = parser.parse_args()

    if not SEMVER.match(args.version):
        parser.error(f"--version must be X.Y.Z, got {args.version!r}")
    if args.build < 1:
        parser.error("--build must be 1 or more")

    version = f"{args.version}-{args.build}"
    mtime = args.mtime if args.mtime is not None else int(time.time())
    output = args.output_dir / f"{PACKAGE}-{version}.spk"
    build_spk(version, args.image, output, mtime)
    print(output)


if __name__ == "__main__":
    main()
