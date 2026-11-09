"""A file's blob is stored under the file's own name.

Django rewrites the name a FileField saves under - "a b.txt" becomes
"a_b.txt", "a+b (1).txt" becomes "ab_1.txt" - so two files whose names
differ only in what it rewrites shared one key, and the files storage writes
over a taken key: the second upload replaced the first file's content.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import FileService

User = get_user_model()


class StorageNameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="namer", password="pw")

    def upload(self, name, data):
        return FileService.create_file(
            self.user, name, content=ContentFile(data, name=name)
        )

    def read(self, file_obj):
        file_obj.refresh_from_db()
        with file_obj.content.open("rb") as handle:
            return handle.read()

    def test_names_django_rewrites_alike_keep_a_blob_each(self):
        first = self.upload("a b.txt", b"first")
        second = self.upload("a_b.txt", b"second")

        self.assertEqual(self.read(first), b"first")
        self.assertEqual(self.read(second), b"second")

    def test_the_blob_sits_at_the_node_tree_path(self):
        folder = FileService.create_folder(self.user, "My Docs")
        f = FileService.create_file(
            self.user,
            "a+b (1).txt",
            parent=folder,
            content=ContentFile(b"x", name="a+b (1).txt"),
        )

        self.assertEqual(f.content.name, "files/users/namer/My Docs/a+b (1).txt")
