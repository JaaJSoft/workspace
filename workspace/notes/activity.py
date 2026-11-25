from django.utils.translation import gettext

from workspace.core.activity_registry import UsageHeadline
from workspace.files.activity import FileFeedActivityProvider


class NotesActivityProvider(FileFeedActivityProvider):
    notes = True
    stat_key = "total_notes"

    def event_url(self, file):
        return f"/notes?file={file.pk}"

    def get_headline(self, stats):
        return UsageHeadline(stats["total_notes"], gettext("Notes"))
