"""The WebDAV tree: one top-level folder per tree, and a path resolved in the
tree its first segment names.

A tree path is only unique inside its tree. Served from one root, an own
folder, a group's root folder and a shared file of the same path answered to
the same URL, and a write aimed at the first could land on another.
"""

import re
import uuid
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from wsgidav.dav_error import HTTP_FORBIDDEN, DAVError

from workspace.common.dav.auth import clear_auth_cache
from workspace.common.tests.wsgi import served_in_test
from workspace.files.models import File, FileShare
from workspace.files.services import tree
from workspace.files.tests import test_webdav
from workspace.files.tests.test_webdav import DAV, DAV_HOME, HOME
from workspace.files.webdav import namespace
from workspace.files.webdav.namespace import GROUPS, PERSONAL, SHARED
from workspace.files.webdav.provider import WorkspaceDAVProvider
from workspace.files.webdav.resources import (
    FileResource,
    FolderResource,
    GroupsCollection,
    PersonalCollection,
    RootCollection,
    SharedCollection,
    _dest_parent,
)

User = get_user_model()

_LOCK_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?><lockinfo xmlns="DAV:">'
    b"<lockscope><exclusive/></lockscope><locktype><write/></locktype>"
    b"</lockinfo>"
)


class _DavTestCase(TestCase):
    """*alice* over WebDAV, next to *bob* who owns files of his own."""

    _request = test_webdav.WebDAVIntegrationTests._request

    def setUp(self):
        from workspace.files.webdav.app import create_webdav_app

        # A fresh app per test: wsgidav keeps DAV locks in memory.
        self._app = served_in_test(create_webdav_app())
        clear_auth_cache()
        self.other = User.objects.create_user(username="bob", password="pass123")
        self.user = User.objects.create_user(username="alice", password="pass123")
        self.auth = test_webdav._basic_auth_header("alice", "pass123")
        self.environ = test_webdav._make_environ(self.user)
        self.provider = WorkspaceDAVProvider()

    def _file(self, owner, name, body=b"", parent=None, **kwargs):
        return tree.create_file(
            owner, name, parent=parent, content=ContentFile(body, name=name), **kwargs
        )

    def _share(self, node, permission=FileShare.Permission.READ_WRITE, **target):
        target = target or {"shared_with": self.user}
        return FileShare.objects.create(
            file=node, shared_by=node.owner, permission=permission, **target
        )

    def _body(self, file_obj):
        file_obj.refresh_from_db()
        with file_obj.content.open("rb") as f:
            return f.read()

    def _resolve(self, path):
        return self.provider.get_resource_inst(path, self.environ)

    def _answer(self, method, path):
        """What a request is answered, less what any answer echoes: the path
        asked for and the time."""
        code, _, body = self._request(method, path)
        for echoed in (quote(path), path):
            body = body.replace(echoed.encode(), b"")
        return code, re.sub(rb"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+", b"", body)

    def _listed(self, path):
        code, _, body = self._request("PROPFIND", path, headers={"Depth": "1"})
        self.assertEqual(code, 207)
        return body.decode()


class SamePathTests(_DavTestCase):
    """Nodes of different trees that carry one tree path.

    Each of these failed against a root that merged the trees: the lookup
    matched any reachable row of that path and kept the first.
    """

    def setUp(self):
        super().setUp()
        # Theirs first: the rows an unscoped lookup picks.
        their_year = tree.create_folder(self.other, "2023")
        self.their_quarter = tree.create_folder(self.other, "Q1", parent=their_year)
        self.their_report = self._file(
            self.other, "report.txt", b"theirs", parent=self.their_quarter
        )
        my_year = tree.create_folder(self.user, "2023")
        self.my_quarter = tree.create_folder(self.user, "Q1", parent=my_year)
        self.my_report = self._file(
            self.user, "report.txt", b"mine", parent=self.my_quarter
        )

    def test_a_shared_folder_does_not_capture_the_path_of_an_own_folder(self):
        self._share(self.their_quarter)

        resource = self._resolve(f"{HOME}/2023/Q1")

        self.assertEqual(resource._file.pk, self.my_quarter.pk)
        self.assertEqual(resource.get_member_names(), ["report.txt"])
        self.assertEqual(resource.get_member("report.txt")._file.pk, self.my_report.pk)

    def test_a_node_is_looked_up_in_the_own_tree(self):
        self._share(self.their_quarter)
        self._share(self.their_report)

        found = namespace.tree_node(self.user, [PERSONAL, "2023", "Q1", "report.txt"])

        self.assertEqual(found.pk, self.my_report.pk)

    def test_a_destination_folder_is_looked_up_in_the_own_tree(self):
        self._share(self.their_quarter)

        parent = _dest_parent(self.user, [PERSONAL, "2023", "Q1"], "MOVE")

        self.assertEqual(parent.pk, self.my_quarter.pk)

    def test_put_to_an_own_file_leaves_a_shared_file_of_the_same_path_alone(self):
        self._share(self.their_report)

        code, _, _ = self._request("PUT", f"{HOME}/2023/Q1/report.txt", body=b"edited")

        self.assertLess(code, 300)
        self.assertEqual(self._body(self.their_report), b"theirs")
        self.assertEqual(self._body(self.my_report), b"edited")

    def test_put_of_a_new_file_lands_in_the_own_folder(self):
        self._share(self.their_quarter)

        code, _, _ = self._request("PUT", f"{HOME}/2023/Q1/new.txt", body=b"new")

        self.assertEqual(code, 201)
        self.assertFalse(self.their_quarter.children.filter(name="new.txt").exists())
        self.assertTrue(self.my_quarter.children.filter(name="new.txt").exists())

    def test_move_into_an_own_folder_does_not_land_in_a_shared_one(self):
        self._share(self.their_quarter)
        self._request("PUT", f"{HOME}/new.txt", body=b"new")

        code, _, _ = self._request(
            "MOVE",
            f"{HOME}/new.txt",
            headers={"Destination": f"{DAV_HOME}/2023/Q1/new.txt"},
        )

        self.assertEqual(code, 201)
        self.assertFalse(self.their_quarter.children.filter(name="new.txt").exists())
        self.assertTrue(self.my_quarter.children.filter(name="new.txt").exists())

    def test_a_lock_on_an_own_file_is_not_put_on_a_shared_one(self):
        self._share(self.their_report)

        code, _, _ = self._request(
            "LOCK",
            f"{HOME}/2023/Q1/report.txt",
            body=_LOCK_BODY,
            headers={"Content-Type": "application/xml", "Depth": "0"},
        )

        self.assertEqual(code, 200)
        self.their_report.refresh_from_db()
        self.my_report.refresh_from_db()
        self.assertIsNone(self.their_report.locked_by_id)
        self.assertEqual(self.my_report.locked_by, self.user)

    def test_the_shared_file_keeps_an_address_of_its_own(self):
        self._share(self.their_report)

        code, _, _ = self._request("PUT", f"/{SHARED}/report.txt", body=b"edited")

        self.assertLess(code, 300)
        self.assertEqual(self._body(self.their_report), b"edited")
        self.assertEqual(self._body(self.my_report), b"mine")

    def test_a_group_folder_does_not_capture_the_path_of_an_own_folder(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        theirs = tree.create_folder(self.other, "Team", group=team)
        mine = tree.create_folder(self.user, "Team")

        self.assertEqual(self._resolve(f"{HOME}/Team")._file.pk, mine.pk)
        self.assertEqual(self._resolve(f"/{GROUPS}/Team")._file.pk, theirs.pk)

        self._request("PUT", f"{HOME}/Team/notes.txt", body=b"mine")
        self._request("PUT", f"/{GROUPS}/Team/notes.txt", body=b"the team's")

        self.assertEqual(self._body(mine.children.get()), b"mine")
        self.assertEqual(self._body(theirs.children.get()), b"the team's")

    def test_a_move_between_same_named_folders_crosses_trees(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        theirs = tree.create_folder(self.other, "Team", group=team)
        mine = tree.create_folder(self.user, "Team")
        self._request("PUT", f"{HOME}/Team/notes.txt", body=b"notes")

        code, _, _ = self._request(
            "MOVE",
            f"{HOME}/Team/notes.txt",
            headers={"Destination": f"{DAV}/{GROUPS}/Team/notes.txt"},
        )

        self.assertEqual(code, 201)
        self.assertFalse(mine.children.exists())
        self.assertEqual(theirs.children.get().group, team)


class LayoutTests(_DavTestCase):
    """The root, the three folders it lists, and what is refused on them."""

    TOP_LEVEL = (HOME, f"/{GROUPS}", f"/{SHARED}")

    def test_the_root_lists_the_three_trees_and_nothing_else(self):
        self._file(self.user, "mine.txt")
        self._share(self._file(self.other, "theirs.txt"))

        root = self._resolve("/")

        self.assertIsInstance(root, RootCollection)
        self.assertEqual(root.get_member_names(), [PERSONAL, GROUPS, SHARED])
        listed = self._listed("/")
        self.assertNotIn("mine.txt", listed)
        self.assertNotIn("theirs.txt", listed)

    def test_each_top_level_folder_resolves_to_its_collection(self):
        for path, collection in zip(
            self.TOP_LEVEL,
            (PersonalCollection, GroupsCollection, SharedCollection),
            strict=True,
        ):
            with self.subTest(path=path):
                self.assertIsInstance(self._resolve(path), collection)
                self.assertIsInstance(self._resolve(f"{path}/"), collection)
                self.assertIsInstance(
                    self._resolve("/").get_member(path.strip("/")), collection
                )

    def test_any_other_root_name_is_unknown(self):
        tree.create_folder(self.user, "Documents")

        for path in ("/Documents", "/my files", "/Elsewhere/x.txt"):
            with self.subTest(path=path):
                self.assertIsNone(self._resolve(path))
                self.assertEqual(self._request("GET", path)[0], 404)

    def test_the_layout_folders_have_the_dates_davfs2_needs(self):
        for path in ("/", *self.TOP_LEVEL):
            with self.subTest(path=path):
                code, _, body = self._request("PROPFIND", path, headers={"Depth": "0"})
                self.assertEqual(code, 207)
                self.assertIn(b"getlastmodified", body)
                self.assertIn(b"creationdate", body)

    def test_nothing_is_created_outside_a_tree(self):
        for folder in ("", f"/{GROUPS}", f"/{SHARED}"):
            with self.subTest(folder=folder):
                self.assertEqual(self._request("PUT", f"{folder}/new.txt")[0], 403)
                self.assertEqual(self._request("MKCOL", f"{folder}/New")[0], 403)
                # A LOCK on a name nothing holds creates the file it locks.
                code, _, _ = self._request(
                    "LOCK",
                    f"{folder}/locked.txt",
                    body=_LOCK_BODY,
                    headers={"Content-Type": "application/xml", "Depth": "0"},
                )
                self.assertEqual(code, 403)
        self.assertFalse(File.objects.exists())

    def test_nothing_is_moved_or_copied_outside_a_tree(self):
        mine = self._file(self.user, "mine.txt", b"mine")
        tree.create_folder(self.user, "Docs")

        for verb in ("MOVE", "COPY"):
            for source in ("mine.txt", "Docs"):
                for folder in ("", f"/{GROUPS}", f"/{SHARED}"):
                    with self.subTest(verb=verb, source=source, folder=folder):
                        code, _, _ = self._request(
                            verb,
                            f"{HOME}/{source}",
                            headers={"Destination": f"{DAV}{folder}/{source}"},
                        )
                        self.assertEqual(code, 403)
        self.assertEqual(File.objects.filter(deleted_at__isnull=True).count(), 2)
        mine.refresh_from_db()
        self.assertIsNone(mine.parent_id)

    def test_deleting_a_layout_folder_trashes_nothing(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        root = tree.create_folder(self.user, "Team", group=team)
        self._file(self.user, "group.txt", parent=root)
        docs = tree.create_folder(self.user, "Docs")
        self._file(self.user, "mine.txt", parent=docs)
        self._share(self._file(self.user, "shared.txt"), shared_with=self.other)

        for path in ("/", *self.TOP_LEVEL):
            with self.subTest(path=path):
                self.assertEqual(self._request("DELETE", path)[0], 403)

        self.assertFalse(File.objects.filter(deleted_at__isnull=False).exists())

    def test_a_layout_folder_is_neither_moved_nor_copied_nor_replaced(self):
        docs = tree.create_folder(self.user, "Docs")
        self._file(self.user, "mine.txt", parent=docs)

        for verb in ("MOVE", "COPY"):
            for path in self.TOP_LEVEL:
                with self.subTest(verb=verb, path=path):
                    code, _, _ = self._request(
                        verb, path, headers={"Destination": f"{DAV_HOME}/Docs/Copy"}
                    )
                    self.assertEqual(code, 403)
        for path in self.TOP_LEVEL:
            with self.subTest(replaced=path):
                code, _, _ = self._request(
                    "MOVE",
                    f"{HOME}/Docs",
                    headers={"Destination": f"{DAV}{quote(path)}", "Overwrite": "T"},
                )
                self.assertEqual(code, 403)

        self.assertEqual(File.objects.filter(deleted_at__isnull=True).count(), 2)
        docs.refresh_from_db()
        self.assertIsNone(docs.parent_id)

    def test_a_layout_folder_refuses_as_a_resource_too(self):
        """Not only through wsgidav's hooks: whatever calls it is refused."""
        for path in ("/", f"/{GROUPS}", f"/{SHARED}"):
            resource = self._resolve(path)
            for call in (
                lambda r: r.create_empty_resource("new.txt"),
                lambda r: r.create_collection("New"),
                lambda r: r.delete(),
                lambda r: r.handle_delete(),
                lambda r: r.handle_move(f"{HOME}/x"),
                lambda r: r.handle_copy(f"{HOME}/x", depth_infinity=True),
            ):
                with self.assertRaises(DAVError) as raised:
                    call(resource)
                self.assertEqual(raised.exception.value, HTTP_FORBIDDEN)

    def test_dav_path_is_where_a_node_is_served(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        root = tree.create_folder(self.user, "Team", group=team)
        in_group = self._file(self.user, "group.txt", parent=root)
        docs = tree.create_folder(self.user, "Docs")
        mine = self._file(self.user, "mine.txt", parent=docs)

        for node in (root, in_group, docs, mine):
            with self.subTest(node=node.path):
                path = namespace.dav_path(node)
                self.assertEqual(self._resolve(path)._file.pk, node.pk)
        self.assertEqual(namespace.dav_path(mine), f"/{PERSONAL}/Docs/mine.txt")
        self.assertEqual(namespace.dav_path(in_group), f"/{GROUPS}/Team/group.txt")
        self.assertEqual(namespace.personal_dav_path("Docs"), f"/{PERSONAL}/Docs")


class PersonalTreeTests(_DavTestCase):
    def test_it_holds_the_own_personal_nodes_only(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        tree.create_folder(self.user, "Team", group=team)
        tree.create_folder(self.user, "Docs")
        self._file(self.user, "mine.txt")
        self._share(self._file(self.other, "theirs.txt"))

        self.assertEqual(
            sorted(self._resolve(HOME).get_member_names()), ["Docs", "mine.txt"]
        )

    def test_a_shortcut_is_not_served(self):
        from workspace.files.services.shortcuts import create_shortcut

        target = self._file(self.user, "mine.txt")
        folder = tree.create_folder(self.user, "Docs")
        shortcut = create_shortcut(self.user, target, folder)

        self.assertEqual(self._resolve(f"{HOME}/Docs").get_member_names(), [])
        self.assertIsNone(self._resolve(f"{HOME}/Docs/{shortcut.name}"))

    def test_a_node_whose_path_mixes_both_unicode_forms_is_found(self):
        """A folder named before names were composed holds a file named since."""
        decomposed = "Résumé"
        folder = tree.create_folder(self.user, "placeholder")
        File.objects.filter(pk=folder.pk).update(name=decomposed, path=decomposed)
        note = self._file(self.user, "été.txt", parent=folder)
        File.objects.filter(pk=note.pk).update(path=f"{decomposed}/été.txt")

        found = self._resolve(f"{HOME}/{decomposed}/été.txt")

        self.assertEqual(found._file.pk, note.pk)


class GroupsTreeTests(_DavTestCase):
    def setUp(self):
        super().setUp()
        self.team = Group.objects.create(name="Team")
        self.user.groups.add(self.team)
        self.other.groups.add(self.team)
        self.root = tree.create_folder(self.other, "Team", group=self.team)
        self.notes = self._file(self.other, "notes.txt", b"notes", parent=self.root)

    def test_it_lists_the_root_folders_of_the_own_groups(self):
        elsewhere = Group.objects.create(name="Elsewhere")
        self.other.groups.add(elsewhere)
        tree.create_folder(self.other, "Elsewhere", group=elsewhere)
        tree.create_folder(self.user, "Personal")

        groups = self._resolve(f"/{GROUPS}")

        self.assertEqual(groups.get_member_names(), ["Team"])
        self.assertIsInstance(groups.get_member("Team"), FolderResource)
        self.assertIsNone(groups.get_member("Elsewhere"))

    def test_a_group_tree_is_read_and_written_by_its_members(self):
        code, _, body = self._request("GET", f"/{GROUPS}/Team/notes.txt")
        self.assertEqual((code, body), (200, b"notes"))

        self.assertEqual(self._request("MKCOL", f"/{GROUPS}/Team/Sub")[0], 201)
        code, _, _ = self._request("PUT", f"/{GROUPS}/Team/Sub/new.txt", body=b"new")

        self.assertEqual(code, 201)
        created = File.objects.get(name="new.txt")
        self.assertEqual(created.group, self.team)
        self.assertEqual(created.path, "Team/Sub/new.txt")

    def test_the_tree_of_a_group_the_user_is_not_in_answers_like_no_tree(self):
        self.user.groups.remove(self.team)

        hidden = self._answer("GET", f"/{GROUPS}/Team/notes.txt")
        absent = self._answer("GET", f"/{GROUPS}/{uuid.uuid4().hex}/notes.txt")

        self.assertEqual(hidden, absent)
        self.assertEqual(hidden[0], 404)
        self.assertEqual(self._request("PUT", f"/{GROUPS}/Team/new.txt")[0], 409)
        self.assertFalse(File.objects.filter(name="new.txt").exists())

    def test_a_former_member_no_longer_reaches_what_they_created_there(self):
        """Owning a group node grants nothing once its owner left the group."""
        code, _, _ = self._request("PUT", f"/{GROUPS}/Team/mine.txt", body=b"mine")
        self.assertEqual(code, 201)
        mine = File.objects.get(name="mine.txt", owner=self.user)
        self.user.groups.remove(self.team)

        for path in (f"/{GROUPS}/Team/mine.txt", f"{HOME}/Team/mine.txt"):
            with self.subTest(path=path):
                self.assertEqual(self._request("GET", path)[0], 404)
                self.assertGreaterEqual(self._request("PUT", path, body=b"x")[0], 400)
        self.assertEqual(self._resolve(f"/{GROUPS}").get_member_names(), [])
        self.assertEqual(self._resolve(HOME).get_member_names(), [])
        self.assertEqual(self._resolve(f"/{SHARED}").get_member_names(), [])
        self.assertEqual(self._body(mine), b"mine")

    def test_a_group_root_folder_stays_where_it_is(self):
        self.assertEqual(self._request("DELETE", f"/{GROUPS}/Team")[0], 403)
        for destination in (f"{DAV}/{GROUPS}/Renamed", f"{DAV_HOME}/Team"):
            with self.subTest(destination=destination):
                code, _, _ = self._request(
                    "MOVE", f"/{GROUPS}/Team", headers={"Destination": destination}
                )
                self.assertEqual(code, 403)

        self.root.refresh_from_db()
        self.assertIsNone(self.root.deleted_at)
        self.assertEqual((self.root.name, self.root.group), ("Team", self.team))
        self.notes.refresh_from_db()
        self.assertIsNone(self.notes.deleted_at)


class SharedTreeTests(_DavTestCase):
    def test_it_lists_the_files_shared_with_the_user(self):
        nested = tree.create_folder(self.other, "Private")
        self._share(self._file(self.other, "a.txt", b"a", parent=nested))
        self._file(self.other, "unshared.txt", parent=nested)
        self._file(self.user, "mine.txt")

        shared = self._resolve(f"/{SHARED}")

        self.assertEqual(shared.get_member_names(), ["a.txt"])
        self.assertIsInstance(shared.get_member("a.txt"), FileResource)
        code, _, body = self._request("GET", f"/{SHARED}/a.txt")
        self.assertEqual((code, body), (200, b"a"))

    def test_it_lists_what_a_group_share_reaches(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        self._share(self._file(self.other, "for-team.txt"), shared_with_group=team)

        self.assertEqual(
            self._resolve(f"/{SHARED}").get_member_names(), ["for-team.txt"]
        )

    def test_a_file_of_an_own_tree_is_served_there_only(self):
        """One address per file, whatever shares also reach it."""
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        self.other.groups.add(team)
        root = tree.create_folder(self.other, "Team", group=team)
        self._share(self._file(self.other, "group.txt", parent=root))
        self._share(self._file(self.user, "mine.txt"), shared_with_group=team)

        self.assertEqual(self._resolve(f"/{SHARED}").get_member_names(), [])
        self.assertIsNone(self._resolve(f"/{SHARED}/mine.txt"))
        self.assertIsNone(self._resolve(f"/{SHARED}/group.txt"))
        self.assertIsNotNone(self._resolve(f"{HOME}/mine.txt"))
        self.assertIsNotNone(self._resolve(f"/{GROUPS}/Team/group.txt"))

    def test_a_file_of_a_group_the_user_is_not_in_is_served_when_shared(self):
        elsewhere = Group.objects.create(name="Elsewhere")
        self.other.groups.add(elsewhere)
        root = tree.create_folder(self.other, "Elsewhere", group=elsewhere)
        self._share(self._file(self.other, "theirs.txt", b"theirs", parent=root))

        code, _, body = self._request("GET", f"/{SHARED}/theirs.txt")

        self.assertEqual((code, body), (200, b"theirs"))
        self.assertIsNone(self._resolve(f"/{GROUPS}/Elsewhere/theirs.txt"))

    def test_same_named_files_are_told_apart_by_their_owner(self):
        carol = User.objects.create_user(username="carol", password="pass123")
        from_bob = self._share(self._file(self.other, "report.txt", b"bob")).file
        from_carol = self._share(self._file(carol, "Report.txt", b"carol")).file
        folder = tree.create_folder(self.other, "Old")
        older = self._share(self._file(self.other, "report.txt", parent=folder)).file
        alone = self._share(self._file(carol, "notes.txt")).file

        listing = namespace.shared_listing(self.user)

        self.assertEqual(
            {name: file_obj.pk for name, file_obj in listing.items()},
            {
                "report (bob).txt": from_bob.pk,
                "Report (carol).txt": from_carol.pk,
                "report (bob 2).txt": older.pk,
                "notes.txt": alone.pk,
            },
        )
        # No shared file answers to the name they have in common.
        self.assertIsNone(self._resolve(f"/{SHARED}/report.txt"))
        self.assertEqual(self._request("GET", f"/{SHARED}/report.txt")[0], 404)
        code, _, body = self._request("GET", f"/{SHARED}/Report (carol).txt")
        self.assertEqual((code, body), (200, b"carol"))

    def test_a_told_apart_name_never_takes_the_name_of_another_file(self):
        carol = User.objects.create_user(username="carol", password="pass123")
        from_bob = self._share(self._file(self.other, "report.txt")).file
        self._share(self._file(carol, "report.txt"))
        named_so = self._share(self._file(carol, "report (bob).txt")).file

        listing = namespace.shared_listing(self.user)

        self.assertEqual(listing["report (bob).txt"].pk, named_so.pk)
        self.assertEqual(listing["report (bob 2).txt"].pk, from_bob.pk)
        self.assertEqual(len(listing), 3)

    def test_every_listed_name_resolves_to_the_file_it_lists(self):
        carol = User.objects.create_user(username="carol", password="pass123")
        for owner, name in (
            (self.other, "report.txt"),
            (carol, "report.txt"),
            (carol, "report (bob).txt"),
            (carol, "notes.txt"),
            (self.other, "été.txt"),
        ):
            self._share(self._file(owner, name))

        listing = namespace.shared_listing(self.user)

        self.assertEqual(len(listing), 5)
        for name, file_obj in listing.items():
            with self.subTest(name=name):
                self.assertEqual(namespace.shared_file(self.user, name).pk, file_obj.pk)
                self.assertEqual(
                    self._resolve(f"/{SHARED}/{name}")._file.pk, file_obj.pk
                )
        decomposed = self._resolve(f"/{SHARED}/été.txt")
        self.assertEqual(decomposed._file.name, "été.txt")

    def test_a_file_not_shared_answers_like_one_that_does_not_exist(self):
        self._file(self.other, "private.txt", b"private")

        hidden = self._answer("GET", f"/{SHARED}/private.txt")
        absent = self._answer("GET", f"/{SHARED}/{uuid.uuid4().hex}.txt")

        self.assertEqual(hidden, absent)
        self.assertEqual(hidden[0], 404)

    def test_an_unshared_file_leaves_the_folder(self):
        share = self._share(self._file(self.other, "theirs.txt"))
        self.assertIsNotNone(self._resolve(f"/{SHARED}/theirs.txt"))

        share.delete()

        self.assertIsNone(self._resolve(f"/{SHARED}/theirs.txt"))
        self.assertEqual(self._resolve(f"/{SHARED}").get_member_names(), [])

    def test_a_trashed_shared_file_leaves_the_folder(self):
        from workspace.files.services import trash

        shared = self._share(self._file(self.other, "theirs.txt")).file
        trash.trash_subtree(shared)

        self.assertIsNone(self._resolve(f"/{SHARED}/theirs.txt"))

    def test_nothing_sits_below_a_shared_file(self):
        self._share(self._file(self.other, "theirs.txt"))

        self.assertIsNone(self._resolve(f"/{SHARED}/theirs.txt/more"))
        self.assertIsNone(namespace.node_at(self.user, [SHARED]))

    def test_a_lock_in_the_shared_folder_goes_on_the_shared_file(self):
        shared = self._share(self._file(self.other, "theirs.txt")).file

        code, _, _ = self._request(
            "LOCK",
            f"/{SHARED}/theirs.txt",
            body=_LOCK_BODY,
            headers={"Content-Type": "application/xml", "Depth": "0"},
        )

        self.assertEqual(code, 200)
        shared.refresh_from_db()
        self.assertEqual(shared.locked_by, self.user)
