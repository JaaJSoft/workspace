from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.imports.models import ImportConnection, ImportJob
from workspace.imports.queries import user_connections_qs, user_jobs_qs

User = get_user_model()


class AccessQueriesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.mine = ImportConnection.objects.create(
            owner=self.alice, provider="webdav", label="mine"
        )
        self.theirs = ImportConnection.objects.create(
            owner=self.bob, provider="webdav", label="theirs"
        )
        self.my_job = ImportJob.objects.create(connection=self.mine, kinds=["files"])
        ImportJob.objects.create(connection=self.theirs, kinds=["files"])

    def test_connections_are_scoped_to_the_owner(self):
        self.assertEqual(list(user_connections_qs(self.alice)), [self.mine])

    def test_jobs_are_scoped_to_the_owner(self):
        self.assertEqual(list(user_jobs_qs(self.alice)), [self.my_job])
