"""MediaInfo: the length and codecs ffprobe reads from audio and video files."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.common.task_priority import BACKGROUND_PRIORITY
from workspace.files.models import File, FileEvent, FileScan, MediaInfo
from workspace.files.services import FileService
from workspace.files.services.event_dispatch import _HANDLERS, run_handlers
from workspace.files.services.media_info import (
    parse_report,
    pending_qs,
    probe_file,
    probe_file_for_event,
)
from workspace.files.tasks import PROBE_CATCH_UP_EXPIRES, probe_media, probe_media_file
from workspace.files.templatetags.file_filters import codec_name, media_duration
from workspace.users.services.settings import set_setting

from .videos import clip_bytes, requires_ffmpeg

User = get_user_model()

_REPORT = {
    "format": {"duration": "12.5"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "mjpeg",
            "disposition": {"attached_pic": 1},
        },
        {"codec_type": "audio", "codec_name": "aac"},
        {"codec_type": "video", "codec_name": "hevc"},
    ],
}


class ParseReportTests(SimpleTestCase):
    def test_duration_and_codecs(self):
        self.assertEqual(
            parse_report(_REPORT),
            {"duration": 12.5, "video_codec": "hevc", "audio_codec": "aac"},
        )

    def test_an_empty_report(self):
        self.assertEqual(
            parse_report({}), {"duration": None, "video_codec": "", "audio_codec": ""}
        )


def catch_up():
    """Run the hourly pass, then every probe it queued; return the queue calls."""
    with patch.object(probe_media_file, "apply_async") as queue:
        stats = probe_media.apply().get()
    for call in queue.call_args_list:
        probe_media_file.apply(args=call.kwargs["args"])
    return stats, queue.call_args_list


class MediaInfoTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def _upload(self, name, data=None):
        return FileService.create_file(
            owner=self.user,
            name=name,
            content=ContentFile(clip_bytes(name) if data is None else data, name=name),
        )

    def _pending(self):
        return set(pending_qs().values_list("pk", flat=True))


@requires_ffmpeg
class ProbeFileTests(MediaInfoTestCase):
    def test_a_video(self):
        f = self._upload("clip_iphone.mov")

        info = probe_file(f)

        self.assertEqual(
            (info.duration, info.video_codec, info.audio_codec), (3.0, "h264", "")
        )
        self.assertEqual(info.content_hash, f.content_hash)
        self.assertEqual(self._pending(), set())

    def test_an_audio_file(self):
        info = probe_file(self._upload("clip.ogg"))

        self.assertEqual(
            (round(info.duration), info.video_codec, info.audio_codec), (2, "", "opus")
        )

    def test_a_file_ffprobe_cannot_read_gets_an_empty_row(self):
        f = self._upload("broken.mp4", b"\x00\x00\x00\x18ftypmp42 truncated")
        File.objects.filter(pk=f.pk).update(type="mp4")
        f.refresh_from_db()

        with self.assertLogs("workspace.files.services.media_info", "INFO"):
            info = probe_file(f)

        self.assertIsNone(info.duration)
        self.assertEqual(self._pending(), set())


class ProbeGuardTests(MediaInfoTestCase):
    def test_other_files_are_not_probed(self):
        f = self._upload("notes.txt", b"some text")

        self.assertIsNone(probe_file(f))
        self.assertEqual(self._pending(), set())

    @patch("workspace.files.services.ffmpeg.FFPROBE", None)
    def test_without_ffprobe_nothing_is_written_and_the_file_stays_pending(self):
        f = self._upload("clip.webm")

        self.assertIsNone(probe_file(f))
        self.assertEqual(catch_up()[0], {"queued": 0})
        self.assertFalse(MediaInfo.objects.exists())
        self.assertEqual(self._pending(), {f.pk})

    def test_a_missing_blob_writes_nothing(self):
        f = self._upload("clip.webm")
        f.content.storage.delete(f.content.name)

        with self.assertLogs("workspace.files.services.media_info", "WARNING"):
            self.assertIsNone(probe_file(f))
        self.assertFalse(MediaInfo.objects.exists())

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_a_quarantined_file_is_never_read(self):
        f = self._upload("clip.webm")
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        with patch("workspace.files.services.ffmpeg.probe") as probe:
            self.assertIsNone(probe_file(f))
        probe.assert_not_called()
        self.assertEqual(self._pending(), set())

    def test_content_replaced_while_probing_writes_nothing(self):
        f = self._upload("clip.webm")

        def replaced_meanwhile(path):
            File.objects.filter(pk=f.pk).update(content_hash="f" * 64)
            return _REPORT

        with patch(
            "workspace.files.services.ffmpeg.probe", side_effect=replaced_meanwhile
        ):
            self.assertIsNone(probe_file(f))
        self.assertFalse(MediaInfo.objects.exists())


class PendingTests(MediaInfoTestCase):
    @patch("workspace.files.services.ffmpeg.probe", return_value=_REPORT)
    def test_a_row_describing_replaced_bytes_is_pending(self, _probe):
        f = self._upload("clip.webm")
        probe_file(f)
        self.assertEqual(self._pending(), set())

        FileService.update_content(
            f, ContentFile(clip_bytes("clip_hevc.mp4"), name="clip.webm")
        )

        self.assertEqual(self._pending(), {f.pk})


@patch("workspace.files.services.ffmpeg.probe", return_value=_REPORT)
class CatchUpTests(MediaInfoTestCase):
    def test_fills_in_every_pending_file_and_is_idempotent(self, _probe):
        webm = self._upload("clip.webm")
        ogg = self._upload("clip.ogg")
        self._upload("notes.txt", b"some text")

        self.assertEqual(catch_up()[0], {"queued": 2})
        self.assertEqual(
            set(MediaInfo.objects.values_list("file_id", flat=True)), {webm.pk, ogg.pk}
        )
        self.assertEqual(catch_up()[0], {"queued": 0})

    def test_queues_one_low_priority_task_per_file_expiring_with_the_pass(self, _probe):
        """The backlog never holds up other tasks, and what the workers did not
        reach before the next pass is dropped rather than queued twice."""
        f = self._upload("clip.webm")

        _, calls = catch_up()

        self.assertEqual([c.kwargs["args"] for c in calls], [[str(f.pk)]])
        self.assertEqual(calls[0].kwargs["priority"], BACKGROUND_PRIORITY)
        self.assertEqual(calls[0].kwargs["expires"], PROBE_CATCH_UP_EXPIRES)

    @patch("workspace.files.tasks.PROBE_CATCH_UP_LIMIT", 2)
    def test_one_pass_is_bounded(self, _probe):
        """A backlog larger than one pass drains over the following ones."""
        self._upload("clip.webm")
        self._upload("clip.ogg")
        self._upload("clip_hevc.mp4")

        self.assertEqual(catch_up()[0], {"queued": 2})
        self.assertEqual(catch_up()[0], {"queued": 1})
        self.assertEqual(MediaInfo.objects.count(), 3)

    def test_a_file_probed_since_it_was_queued_is_not_read_again(self, probe):
        """Its upload event may have run while the task waited in the queue."""
        f = self._upload("clip.webm")
        probe_file(f)
        probe.reset_mock()

        status = probe_media_file.apply(args=[str(f.pk)]).get()

        self.assertEqual(status, {"status": "skipped"})
        probe.assert_not_called()

    def test_unknown_or_malformed_id(self, _probe):
        for file_uuid in ("00000000-0000-0000-0000-000000000000", "nope", None):
            with self.subTest(file_uuid=file_uuid):
                self.assertEqual(
                    probe_media_file.apply(args=[file_uuid]).get(),
                    {"status": "skipped"},
                )


class ProbeScheduleTests(SimpleTestCase):
    def test_queued_probes_expire_at_the_next_pass(self):
        entry = settings.CELERY_BEAT_SCHEDULE["probe-media"]

        self.assertEqual(entry["task"], "files.probe_media")
        self.assertEqual(entry["options"], {"expires": entry["schedule"]})
        self.assertEqual(PROBE_CATCH_UP_EXPIRES, entry["schedule"])


class HandlerTests(MediaInfoTestCase):
    def test_subscribed_to_uploads_and_content_replacements(self):
        for action in (FileEvent.Action.CREATED, FileEvent.Action.CONTENT_REPLACED):
            with self.subTest(action=action):
                self.assertIn(probe_file_for_event, _HANDLERS[str(action)])

    @patch("workspace.files.services.ffmpeg.probe", return_value=_REPORT)
    def test_an_upload_is_probed_once_committed(self, _probe):
        with patch(
            "workspace.files.tasks.run_file_event_handlers.delay",
            side_effect=run_handlers,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                f = self._upload("clip.webm")

        self.assertEqual(MediaInfo.objects.get(file=f).duration, 12.5)

    @patch("workspace.files.services.ffmpeg.probe", return_value=_REPORT)
    def test_a_video_replaced_by_something_else_loses_its_row(self, _probe):
        f = self._upload("clip.webm")
        probe_file(f)
        File.objects.filter(pk=f.pk).update(type="txt")
        f.refresh_from_db()

        probe_file_for_event(
            FileEvent(file=f, action=FileEvent.Action.CONTENT_REPLACED)
        )

        self.assertFalse(MediaInfo.objects.exists())

    @patch("workspace.files.services.ffmpeg.probe", return_value=_REPORT)
    def test_trashed_before_the_handler_ran_is_skipped(self, probe):
        f = self._upload("clip.webm")
        FileService.soft_delete(f, acting_user=self.user)
        f.refresh_from_db()

        probe_file_for_event(FileEvent(file=f, action=FileEvent.Action.CREATED))

        probe.assert_not_called()


class FilterTests(SimpleTestCase):
    def test_media_duration_as_a_player_shows_it(self):
        for seconds, shown in (
            (0.2, "0:01"),
            (3.0, "0:03"),
            (59.6, "1:00"),
            (754.4, "12:34"),
            (3723, "1:02:03"),
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(media_duration(seconds), shown)

    def test_codec_names(self):
        self.assertEqual(codec_name("hevc"), "HEVC")
        self.assertEqual(codec_name("h264"), "H.264")
        self.assertEqual(codec_name("theora"), "THEORA")


class MosaicBadgeTests(SimpleTestCase):
    def _render(self, **context):
        return Template(
            '{% include "files/ui/partials/node_preview.html" with icon="video" '
            'icon_color="" duration=duration %}'
        ).render(Context(context))

    def test_a_playable_file_gets_its_length(self):
        self.assertIn("0:42", self._render(duration=42.0))

    def test_no_badge_without_a_length(self):
        self.assertNotIn("data-duration-badge", self._render(duration=None))


class MosaicListingTests(MediaInfoTestCase):
    def setUp(self):
        super().setUp()
        set_setting(self.user, "files", "preferences", {"defaultViewMode": "mosaic"})
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _video_with_info(self, name, duration):
        f = self._upload(name, clip_bytes("clip.webm"))
        MediaInfo.objects.create(
            file=f,
            duration=duration,
            content_hash=f.content_hash,
            probed_at=timezone.now(),
        )
        return f

    def test_video_cards_show_their_length(self):
        self._video_with_info("surf.webm", 754.4)

        self.assertContains(self.client.get("/files"), "12:34")

    def test_the_lengths_cost_no_query_per_card(self):
        self._video_with_info("a.webm", 3.0)
        self.client.get("/files")
        with CaptureQueriesContext(connection) as one:
            self.client.get("/files")

        for i in range(5):
            self._video_with_info(f"more{i}.webm", 3.0)
        with CaptureQueriesContext(connection) as six:
            self.client.get("/files")

        self.assertEqual(len(six), len(one))
