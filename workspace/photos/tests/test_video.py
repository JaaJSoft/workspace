from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase

from workspace.files.services import FileService
from workspace.files.tests.videos import clip_bytes, requires_ffmpeg
from workspace.photos.services.video import VideoMetadata, parse_report, read_metadata

User = get_user_model()
PARIS = ZoneInfo("Europe/Paris")


def _report(*, tags=None, streams=None, duration="12.5"):
    return {
        "format": {"duration": duration, "tags": tags or {}},
        "streams": streams
        if streams is not None
        else [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}
        ],
    }


class RecordingDateTests(SimpleTestCase):
    def test_quicktime_creation_date_keeps_its_offset(self):
        meta = parse_report(
            _report(
                tags={"com.apple.quicktime.creationdate": "2024-07-14T12:30:01+0200"}
            )
        )

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 10, 30, 1, tzinfo=UTC))
        self.assertEqual(meta.taken_at.utcoffset(), timedelta(hours=2))

    def test_quicktime_date_wins_over_creation_time(self):
        meta = parse_report(
            _report(
                tags={
                    "creation_time": "2024-07-14T09:00:00.000000Z",
                    "com.apple.quicktime.creationdate": "2024-07-14T12:30:01+0200",
                }
            )
        )

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 10, 30, 1, tzinfo=UTC))

    def test_quicktime_date_without_offset_reads_in_the_owners_timezone(self):
        meta = parse_report(
            _report(tags={"com.apple.quicktime.creationdate": "2024-07-14T12:30:01"}),
            default_tz=PARIS,
        )

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 10, 30, 1, tzinfo=UTC))

    def test_creation_time_is_utc(self):
        meta = parse_report(
            _report(tags={"creation_time": "2024-07-14T10:30:01.000000Z"}),
            default_tz=PARIS,
        )

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 10, 30, 1, tzinfo=UTC))

    def test_creation_time_of_the_video_stream_as_a_last_resort(self):
        meta = parse_report(
            _report(
                streams=[
                    {
                        "codec_type": "video",
                        "tags": {"creation_time": "2024-07-14T10:30:01Z"},
                    }
                ]
            )
        )

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 10, 30, 1, tzinfo=UTC))

    def test_format_epochs_and_garbage_are_no_date(self):
        for raw in (
            "1904-01-01T00:00:00.000000Z",
            "1970-01-01T00:00:00.000000Z",
            "not a date",
            "",
        ):
            with self.subTest(raw=raw):
                meta = parse_report(_report(tags={"creation_time": raw}))
                self.assertIsNone(meta.taken_at)

    def test_no_date_at_all(self):
        self.assertIsNone(parse_report(_report()).taken_at)


class DimensionsTests(SimpleTestCase):
    def _size(self, stream):
        meta = parse_report(_report(streams=[{"codec_type": "video", **stream}]))
        return meta.width, meta.height

    def test_as_stored_without_rotation(self):
        self.assertEqual(self._size({"width": 1920, "height": 1080}), (1920, 1080))

    def test_display_matrix_rotation_swaps_them(self):
        for rotation in (90, -90, 270):
            with self.subTest(rotation=rotation):
                self.assertEqual(
                    self._size(
                        {
                            "width": 1920,
                            "height": 1080,
                            "side_data_list": [
                                {
                                    "side_data_type": "Display Matrix",
                                    "rotation": rotation,
                                }
                            ],
                        }
                    ),
                    (1080, 1920),
                )

    def test_upside_down_keeps_them(self):
        self.assertEqual(
            self._size(
                {"width": 1920, "height": 1080, "side_data_list": [{"rotation": 180}]}
            ),
            (1920, 1080),
        )

    def test_rotate_tag_of_older_muxers(self):
        self.assertEqual(
            self._size({"width": 1920, "height": 1080, "tags": {"rotate": "90"}}),
            (1080, 1920),
        )

    def test_unreadable_values(self):
        self.assertEqual(
            self._size({"width": 0, "height": "x", "tags": {"rotate": "sideways"}}),
            (None, None),
        )


class StreamTests(SimpleTestCase):
    def test_cover_picture_is_not_the_video(self):
        meta = parse_report(
            _report(
                streams=[
                    {"codec_type": "audio", "codec_name": "aac"},
                    {
                        "codec_type": "video",
                        "codec_name": "mjpeg",
                        "width": 600,
                        "height": 600,
                        "disposition": {"attached_pic": 1},
                    },
                    {
                        "codec_type": "video",
                        "codec_name": "hevc",
                        "width": 3840,
                        "height": 2160,
                    },
                ]
            )
        )

        self.assertEqual((meta.width, meta.height), (3840, 2160))

    def test_no_video_stream(self):
        meta = parse_report(
            _report(streams=[{"codec_type": "audio", "codec_name": "opus"}])
        )

        self.assertEqual((meta.width, meta.height), (None, None))

    def test_an_empty_report(self):
        self.assertEqual(parse_report({}), VideoMetadata())


class CameraTests(SimpleTestCase):
    def test_apple_tags(self):
        meta = parse_report(
            _report(
                tags={
                    "com.apple.quicktime.make": "Apple",
                    "com.apple.quicktime.model": "iPhone 15\x00",
                }
            )
        )

        self.assertEqual((meta.camera_make, meta.camera_model), ("Apple", "iPhone 15"))

    def test_android_tags(self):
        meta = parse_report(
            _report(
                tags={
                    "com.android.manufacturer": "Google",
                    "com.android.model": "Pixel 8",
                }
            )
        )

        self.assertEqual((meta.camera_make, meta.camera_model), ("Google", "Pixel 8"))

    def test_capped_to_the_column(self):
        meta = parse_report(_report(tags={"com.apple.quicktime.make": "x" * 500}))

        self.assertEqual(len(meta.camera_make), 128)


class LocationTests(SimpleTestCase):
    def _location(self, tags):
        meta = parse_report(_report(tags=tags))
        return meta.latitude, meta.longitude

    def test_quicktime_iso6709(self):
        self.assertEqual(
            self._location(
                {"com.apple.quicktime.location.ISO6709": "+48.8584+002.2945+035.000/"}
            ),
            (48.8584, 2.2945),
        )

    def test_android_location_tag(self):
        self.assertEqual(
            self._location({"location": "-33.8568+151.2153/"}), (-33.8568, 151.2153)
        )

    def test_no_fix_out_of_range_and_garbage_are_dropped(self):
        for raw in (
            "+00.0000+000.0000/",
            "+91.0000+002.0000/",
            "+48.0+181.0/",
            "Paris",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self._location({"location": raw}), (None, None))


@requires_ffmpeg
class ReadMetadataTests(TestCase):
    def test_what_an_iphone_writes(self):
        user = User.objects.create_user(username="alice", password="p")
        f = FileService.create_file(
            owner=user,
            name="clip_iphone.mov",
            content=ContentFile(clip_bytes("clip_iphone.mov")),
        )

        meta = read_metadata(f.content.path)

        self.assertEqual(
            meta,
            VideoMetadata(
                taken_at=datetime(
                    2024, 7, 14, 12, 30, 1, tzinfo=timezone(timedelta(hours=2))
                ),
                width=54,
                height=96,
                camera_make="Apple",
                camera_model="iPhone 15",
                latitude=48.8584,
                longitude=2.2945,
            ),
        )
