import json
import re
from html import unescape

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.files.models import File

User = get_user_model()

JS_STRING = r"'((?:[^'\\]|\\.)*)'"
# The browser decodes &#x27; before Alpine evaluates x-data, so the call must
# still read as iconPicker(uuid, icon, color, endpoint, ...) once it has.
ICON_PICKER = re.compile(
    rf"iconPicker\({JS_STRING}, {JS_STRING}, {JS_STRING}, '/api/v1/files/', "
)


class PropertiesIconEscapingTests(TestCase):
    """A folder's icon and colour are free text any editor of the folder sets."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        self.bob.groups.add(team)
        self.folder = File.objects.create(
            owner=self.alice,
            name="Team",
            node_type=File.NodeType.FOLDER,
            group=team,
            icon="');alert(1);//",
            color="x');alert(2);//",
        )
        self.client.force_login(self.bob)

    def _icon_picker_args(self):
        html = self.client.get(f"/files/properties/{self.folder.uuid}").content.decode()
        x_data = unescape(re.search(r'x-data="(iconPicker\(.*?)"\n', html).group(1))
        match = ICON_PICKER.match(x_data)
        self.assertIsNotNone(match, x_data)
        return [json.loads(f'"{arg}"') for arg in match.groups()]

    def test_icon_and_colour_stay_inside_their_strings(self):
        uuid, icon, color = self._icon_picker_args()
        self.assertEqual(uuid, str(self.folder.uuid))
        self.assertEqual(icon, "');alert(1);//")
        self.assertEqual(color, "x');alert(2);//")
