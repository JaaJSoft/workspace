"""Model weights: downloaded on first use, checked against a pinned sha256.

Knows nothing about faces: any ONNX model the library runs (an image
embedding for similar-photo search, say) declares its ModelFile the same way.

Nothing is fetched at import or at boot. The first analysis on a deployment
downloads what its backend needs into PHOTOS_MODEL_DIR; the Dockerfile's
`face-models` stage runs the same code at build time so an image can ship
them. A file whose hash does not match is never loaded: a truncated download
or a tampered mirror fails loudly instead of producing wrong embeddings.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx2
from django.conf import settings

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024
_DOWNLOAD_TIMEOUT = 60.0

# Paths whose hash was checked by this process: hashing 170 MB on every
# session load would cost more than the load itself.
_verified: set[Path] = set()


class WeightsError(RuntimeError):
    """A model file could not be downloaded or failed its hash check."""


@dataclass(frozen=True)
class ModelFile:
    # Path under PHOTOS_MODEL_DIR.
    name: str
    sha256: str
    # The file itself, or the zip archive holding it as *archive_member*.
    url: str
    archive_member: str = ""
    archive_sha256: str = ""


def model_dir():
    return Path(settings.PHOTOS_MODEL_DIR)


def model_path(model):
    return model_dir() / model.name


def is_present(model):
    """Whether *model* is on disk (its hash is checked when it is loaded)."""
    return model_path(model).is_file()


def ensure(model):
    """The local path of *model*, downloading it first when missing.

    Raises WeightsError when the download fails or the hash does not match.
    """
    ensure_all((model,))
    return model_path(model)


def ensure_all(models):
    """Make every one of *models* present and verified.

    Members of one archive are extracted from a single download of it.
    """
    missing = []
    for model in models:
        path = model_path(model)
        if path in _verified:
            continue
        if path.is_file():
            if _sha256(path) != model.sha256:
                raise WeightsError(
                    f"{path} does not match its pinned sha256: delete it to "
                    f"download it again"
                )
            _verified.add(path)
        else:
            missing.append(model)
    by_url = {}
    for model in missing:
        by_url.setdefault(model.url, []).append(model)
    for url, group in by_url.items():
        _download(url, group)
        _verified.update(model_path(model) for model in group)


def _download(url, models):
    target_dir = model_path(models[0]).parent
    target_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading model weights from %s", url)
    # Written next to the targets and renamed into place: two workers
    # downloading at once each finish their own copy, and a reader never sees
    # half a file.
    with tempfile.TemporaryDirectory(dir=target_dir) as tmp:
        fetched = Path(tmp) / "download"
        digest = _fetch(url, fetched)
        archive_sha256 = models[0].archive_sha256
        if archive_sha256 and digest != archive_sha256:
            raise WeightsError(f"{url} does not match its pinned sha256")
        for model in models:
            source = fetched
            if model.archive_member:
                source = Path(tmp) / model.archive_member.replace("/", "_")
                with zipfile.ZipFile(fetched) as archive, source.open("wb") as out:
                    with archive.open(model.archive_member) as member:
                        while chunk := member.read(_CHUNK):
                            out.write(chunk)
            if _sha256(source) != model.sha256:
                raise WeightsError(f"{model.name} does not match its pinned sha256")
            path = model_path(model)
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, path)


def _fetch(url, target):
    digest = hashlib.sha256()
    try:
        with (
            httpx2.stream(
                "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
            ) as response,
            target.open("wb") as out,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
    except httpx2.HTTPError as exc:
        raise WeightsError(f"cannot download {url}: {exc}") from exc
    return digest.hexdigest()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
