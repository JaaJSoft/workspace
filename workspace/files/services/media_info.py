"""Probe audio and video files for their length and codecs (MediaInfo rows).

Runs off-request: from the file-event handler once an upload or a content
replacement has committed, and from the hourly catch-up (services/catch_up.py)
for whatever that path missed. Nothing is written on a deployment without
ffprobe, so the files are probed as soon as it is installed.
"""

import logging

from django.db.models import F, Q
from django.utils import timezone

from workspace.common.logging import scrub

from ..models import File, FileEvent, MediaInfo
from . import ffmpeg
from .catch_up import register_catch_up
from .event_dispatch import on_file_event
from .scanning.policy import exclude_blocked, is_blocked
from .thumbnails.generation import VIDEO_LABELS

logger = logging.getLogger(__name__)

# Magika's audio labels ffmpeg can decode: MIDI is a score, not a recording.
AUDIO_LABELS = frozenset({"mp3", "ogg", "flac", "wav", "wma", "au"})
MEDIA_INFO_LABELS = AUDIO_LABELS | VIDEO_LABELS

CODEC_FIELD_LENGTH = MediaInfo._meta.get_field("video_codec").max_length


def is_probe_candidate(file_obj):
    """True when *file_obj* is an audio or video file ffprobe may read.

    A quarantined file never is: reading its bytes is exactly what the malware
    policy forbids.
    """
    return (
        file_obj.node_type == File.NodeType.FILE
        and file_obj.type in MEDIA_INFO_LABELS
        and bool(file_obj.content)
        and not is_blocked(file_obj)
    )


def pending_qs(*, reanalyze=False):
    """Live audio and video files whose MediaInfo row is missing or stale.

    With *reanalyze*, every live audio and video file, up to date or not.

    A file whose own hash is empty (registered before hashes existed) only
    counts when it has no row at all: comparing an empty hash would mark it
    stale on every pass, forever.
    """
    qs = exclude_blocked(
        File.objects.alive().with_blob().filter(type__in=MEDIA_INFO_LABELS)
    )
    if reanalyze:
        return qs
    return qs.filter(
        Q(media_info__isnull=True)
        | (~Q(content_hash="") & ~Q(media_info__content_hash=F("content_hash")))
    )


def parse_report(report):
    """The MediaInfo fields an ffprobe report describes."""
    return {
        "duration": ffmpeg.duration(report),
        "video_codec": _codec(ffmpeg.video_stream(report)),
        "audio_codec": _codec(ffmpeg.audio_stream(report)),
    }


def _codec(stream):
    return str(stream.get("codec_name") or "")[:CODEC_FIELD_LENGTH]


def probe_file(file_obj):
    """Probe *file_obj* into its MediaInfo row; return the row or None.

    None means nothing was written: the file is not an audio or video file,
    ffprobe is not installed, the blob could not be opened (the catch-up
    tries again), or the content was replaced while it was being read (the
    replacement's own event takes over). A file ffprobe cannot make sense of
    still gets a row, all empty: it has been looked at, and it would
    otherwise come back on every hourly pass.
    """
    if not ffmpeg.FFPROBE or not is_probe_candidate(file_obj):
        return None

    read_hash = file_obj.content_hash
    try:
        with ffmpeg.local_path(file_obj.content) as path:
            fields = parse_report(ffmpeg.probe(path))
    except ffmpeg.MediaToolError as exc:
        logger.info(
            "ffprobe could not read %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        fields = parse_report({})
    except OSError as exc:
        logger.warning(
            "Cannot probe the blob of %s: %s",
            scrub(file_obj.content.name),
            scrub(str(exc)),
        )
        return None

    current_hash = (
        File.objects.filter(pk=file_obj.pk)
        .values_list("content_hash", flat=True)
        .first()
    )
    if current_hash is None or current_hash != read_hash:
        return None

    info, _ = MediaInfo.objects.update_or_create(
        file_id=file_obj.pk,
        defaults={**fields, "content_hash": read_hash, "probed_at": timezone.now()},
    )
    return info


def refresh_media_info(file_obj):
    """Probe *file_obj* for the catch-up; True when its row was written."""
    return probe_file(file_obj) is not None


def _ffprobe_installed():
    # Read at call time: the probe binary is looked up once at import, but
    # tests switch it off per case.
    return bool(ffmpeg.FFPROBE)


def forget(file_obj):
    """Drop the row of a file that stopped being an audio or video file."""
    MediaInfo.objects.filter(file_id=file_obj.pk).delete()


@on_file_event(FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED)
def probe_file_for_event(event):
    file_obj = event.file
    if file_obj.deleted_at is not None:
        # Trashed before we ran; the catch-up probes it after a restore.
        return
    if is_probe_candidate(file_obj):
        probe_file(file_obj)
    elif event.action == FileEvent.Action.CONTENT_REPLACED:
        forget(file_obj)


register_catch_up(
    "media_info",
    pending=pending_qs,
    process=refresh_media_info,
    enabled=_ffprobe_installed,
)
