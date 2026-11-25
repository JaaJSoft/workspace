"""The WebDAV tree: one top-level folder per tree, and a path resolved in the
tree its first segment names.

A tree path is only unique inside its tree. Served from one root, an own
folder, a group's root folder and a shared file of the same path answered to
the same URL, and a write aimed at the first could land on another.
"""

import re
import unicodedata
import uuid
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from wsgidav.dav_error import HTTP_FORBIDDEN, DAVError

from workspace.common.dav.auth import clear_auth_cache
from workspace.common.tests.wsgi import served_in_test
from workspace.common.text import caseless
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


def _wsgi(path):
    """*path* as a WSGI server hands it over: its UTF-8 bytes, read as latin-1."""
    return path.encode().decode("latin-1")


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

    def _stored_decomposed(self, node):
        """*node* as a row from before names were composed: its name, and the
        end of its path, in the form macOS sends them."""
        name = unicodedata.normalize("NFD", node.name)
        path = f"{node.parent.path}/{name}" if node.parent_id else name
        File.objects.filter(pk=node.pk).update(
            name=name, path=path, name_key=caseless(name)
        )
        node.refresh_from_db()
        return node

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
        folder = self._stored_decomposed(
            tree.create_folder(self.user, "R\u00e9sum\u00e9")
        )
        note = self._file(self.user, "\u00e9t\u00e9.txt", parent=folder)

        found = self._resolve(f"{HOME}/{folder.name}/e\u0301te\u0301.txt")

        self.assertEqual(found._file.pk, note.pk)

    def test_folders_stored_in_different_unicode_forms_lead_to_the_file(self):
        """Above the file, a folder from before names were composed and one
        named since: the path is under neither form of the whole of it."""
        legacy = self._stored_decomposed(
            tree.create_folder(self.user, "R\u00e9sum\u00e9")
        )
        summer = tree.create_folder(self.user, "\u00e9t\u00e9", parent=legacy)
        note = self._file(self.user, "x.txt", parent=summer)
        composed = f"{HOME}/R\u00e9sum\u00e9/\u00e9t\u00e9/x.txt"

        for form in ("NFC", "NFD"):
            with self.subTest(form=form):
                found = self._resolve(unicodedata.normalize(form, composed))
                self.assertIsNotNone(found)
                self.assertEqual(found._file.pk, note.pk)
        self.assertEqual(
            self._resolve(f"{HOME}/{legacy.name}").get_member("\u00e9t\u00e9")._file.pk,
            summer.pk,
        )

    def test_a_name_from_before_names_were_composed_is_found_composed(self):
        note = self._stored_decomposed(self._file(self.user, "\u00e9t\u00e9.txt"))

        found = self._resolve(f"{HOME}/\u00e9t\u00e9.txt")

        self.assertIsNotNone(found)
        self.assertEqual(found._file.pk, note.pk)
        self.assertEqual(
            self._resolve(HOME).get_member("\u00e9t\u00e9.txt")._file.pk, note.pk
        )

    def test_the_case_of_a_name_still_counts(self):
        self._file(self.user, "Notes.txt")

        self.assertIsNone(self._resolve(f"{HOME}/notes.txt"))
        self.assertIsNone(self._resolve(HOME).get_member("notes.txt"))


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

    @staticmethod
    def _told_apart(file_obj, whole=False):
        """The address of a file another shared file has the name of."""
        stem, extension = file_obj.name.rsplit(".", 1)
        tail = file_obj.uuid.hex if whole else file_obj.uuid.hex[-8:]
        return f"{stem} ({file_obj.owner.username} {tail}).{extension}"

    def _addresses(self):
        listing = namespace.shared_listing(self.user)
        return {file_obj.pk: name for name, file_obj in listing.items()}

    def test_same_named_files_are_told_apart_by_their_owner_and_their_own_id(self):
        carol = User.objects.create_user(username="carol", password="pass123")
        from_bob = self._share(self._file(self.other, "report.txt", b"bob")).file
        from_carol = self._share(self._file(carol, "Report.txt", b"carol")).file
        folder = tree.create_folder(self.other, "Old")
        older = self._share(self._file(self.other, "report.txt", parent=folder)).file
        alone = self._share(self._file(carol, "notes.txt")).file

        self.assertEqual(
            self._addresses(),
            {
                from_bob.pk: self._told_apart(from_bob),
                from_carol.pk: self._told_apart(from_carol),
                older.pk: self._told_apart(older),
                alone.pk: "notes.txt",
            },
        )
        # No shared file answers to the name they have in common.
        self.assertIsNone(self._resolve(f"/{SHARED}/report.txt"))
        self.assertEqual(self._request("GET", f"/{SHARED}/report.txt")[0], 404)
        code, _, body = self._request(
            "GET", f"/{SHARED}/{self._told_apart(from_carol)}"
        )
        self.assertEqual((code, body), (200, b"carol"))

    def test_revoking_a_share_never_hands_its_address_to_another_file(self):
        """A client that kept the address of the first would write the second."""
        carol = User.objects.create_user(username="carol", password="pass123")
        first = self._share(self._file(self.other, "report.txt", b"first"))
        folder = tree.create_folder(self.other, "Old")
        second = self._share(
            self._file(self.other, "report.txt", b"second", parent=folder)
        ).file
        self._share(self._file(carol, "report.txt", b"carol"))
        before = self._addresses()

        first.delete()

        after = self._addresses()
        self.assertNotIn(first.file.pk, after)
        self.assertEqual(after[second.pk], before[second.pk])
        self.assertIsNone(self._resolve(f"/{SHARED}/{before[first.file.pk]}"))
        code, _, _ = self._request(
            "PUT", f"/{SHARED}/{before[first.file.pk]}", body=b"meant for the first"
        )
        self.assertGreaterEqual(code, 400)
        self.assertEqual(self._body(second), b"second")

    def test_no_address_passes_to_another_file_when_one_share_goes(self):
        """Whichever share is taken back, among files of one name and files
        named like the addresses of another."""
        carol = User.objects.create_user(username="carol", password="pass123")
        report = self._file(self.other, "report.txt")
        files = [
            report,
            self._file(
                self.other, "report.txt", parent=tree.create_folder(self.other, "Old")
            ),
            self._file(carol, "report.txt"),
            self._file(carol, "Report.TXT", parent=tree.create_folder(carol, "Caps")),
            self._file(carol, self._told_apart(report)),
            self._file(carol, self._told_apart(report, whole=True)),
            self._file(carol, "notes.txt"),
        ]
        shares = {file_obj.pk: self._share(file_obj) for file_obj in files}
        before = {
            name: file_obj.pk
            for name, file_obj in namespace.shared_listing(self.user).items()
        }
        self.assertEqual(len(before), len(files))

        for revoked in files:
            with self.subTest(revoked=revoked.path):
                shares[revoked.pk].delete()
                after = namespace.shared_listing(self.user)
                self.assertEqual(len(after), len(files) - 1)
                for name, file_obj in after.items():
                    if name in before:
                        self.assertEqual(before[name], file_obj.pk, name)
                shares[revoked.pk] = self._share(revoked)

    def test_the_last_file_of_a_name_is_served_under_it_again(self):
        carol = User.objects.create_user(username="carol", password="pass123")
        from_bob = self._share(self._file(self.other, "report.txt", b"bob")).file
        from_carol = self._share(self._file(carol, "report.txt", b"carol"))
        self.assertIsNone(self._resolve(f"/{SHARED}/report.txt"))

        from_carol.delete()

        self.assertEqual(self._addresses(), {from_bob.pk: "report.txt"})
        self.assertEqual(self._resolve(f"/{SHARED}/report.txt")._file.pk, from_bob.pk)

    def test_a_file_named_like_an_address_is_told_apart_whatever_is_shared(self):
        """Listed under its own name, it would hold the address of a file told
        apart, and hand it over the day that file is."""
        carol = User.objects.create_user(username="carol", password="pass123")
        from_bob = self._share(self._file(self.other, "report.txt", b"bob")).file
        named_so = self._share(self._file(carol, self._told_apart(from_bob))).file

        self.assertEqual(
            self._addresses(),
            {from_bob.pk: "report.txt", named_so.pk: self._told_apart(named_so)},
        )
        self.assertIsNone(self._resolve(f"/{SHARED}/{named_so.name}"))

        self._share(self._file(carol, "report.txt"))

        addresses = self._addresses()
        self.assertEqual(addresses[from_bob.pk], named_so.name)
        self.assertEqual(addresses[named_so.pk], self._told_apart(named_so))
        code, _, body = self._request("GET", f"/{SHARED}/{named_so.name}")
        self.assertEqual((code, body), (200, b"bob"))

    def test_files_named_like_both_addresses_of_another_leave_it_its_own(self):
        """Named like its address, then like the one made of its whole id:
        the file left the folder, and the second answered in its place."""
        carol = User.objects.create_user(username="carol", password="pass123")
        report = self._share(self._file(self.other, "report.txt", b"report")).file
        self._share(self._file(carol, "report.txt"))
        address = self._addresses()[report.pk]
        whole = self._told_apart(report, whole=True)
        self._share(self._file(carol, address))
        like_whole = self._share(self._file(carol, whole, b"another")).file

        addresses = self._addresses()

        self.assertEqual(len(addresses), 4)
        self.assertEqual(len({caseless(name) for name in addresses.values()}), 4)
        self.assertEqual(addresses[report.pk], address)
        self.assertEqual(self._resolve(f"/{SHARED}/{address}")._file.pk, report.pk)
        self.assertIsNone(self._resolve(f"/{SHARED}/{whole}"))
        code, _, _ = self._request(
            "PUT", f"/{SHARED}/{whole}", body=b"meant for the report"
        )
        self.assertGreaterEqual(code, 400)
        self.assertEqual(self._body(like_whole), b"another")

    def test_two_files_told_apart_alike_fall_back_on_their_whole_id(self):
        """One name, one owner and one end of id: the address they would
        share is served for neither."""
        twins = [
            File.objects.create(
                uuid=uuid.UUID(f"{start}-0000-7000-8000-0000deadbeef"),
                owner=self.other,
                name="report.txt",
                node_type=File.NodeType.FILE,
                parent=tree.create_folder(self.other, folder),
            )
            for start, folder in (("00000000", "Old"), ("11111111", "New"))
        ]
        for twin in twins:
            self._share(twin)

        addresses = self._addresses()

        self.assertEqual(
            addresses,
            {twin.pk: self._told_apart(twin, whole=True) for twin in twins},
        )
        self.assertIsNone(self._resolve(f"/{SHARED}/{self._told_apart(twins[0])}"))
        for twin in twins:
            found = self._resolve(f"/{SHARED}/{addresses[twin.pk]}")
            self.assertEqual(found._file.pk, twin.pk)

    def test_every_listed_name_resolves_to_the_file_it_lists(self):
        carol = User.objects.create_user(username="carol", password="pass123")
        report = self._file(self.other, "report.txt")
        for file_obj in (
            report,
            self._file(carol, "report.txt"),
            self._file(carol, self._told_apart(report)),
            self._file(carol, self._told_apart(report, whole=True)),
            self._file(carol, "notes.txt"),
            self._file(carol, "notes (draft cafe).txt"),
            self._file(self.other, "\u00e9t\u00e9.txt"),
        ):
            self._share(file_obj)

        listing = namespace.shared_listing(self.user)

        self.assertEqual(len(listing), 7)
        for name, file_obj in listing.items():
            with self.subTest(name=name):
                self.assertEqual(namespace.shared_file(self.user, name).pk, file_obj.pk)
                self.assertEqual(
                    self._resolve(f"/{SHARED}/{name}")._file.pk, file_obj.pk
                )
                member = self._resolve(f"/{SHARED}").get_member(name)
                self.assertEqual(member._file.pk, file_obj.pk)
        decomposed = self._resolve(f"/{SHARED}/e\u0301te\u0301.txt")
        self.assertEqual(decomposed._file.name, "\u00e9t\u00e9.txt")

    def test_a_shared_name_from_before_names_were_composed_is_found_composed(self):
        shared = self._stored_decomposed(self._file(self.other, "caf\u00e9.txt", b"x"))
        self._share(shared)

        for form in ("NFC", "NFD"):
            with self.subTest(form=form):
                name = unicodedata.normalize(form, "caf\u00e9.txt")
                found = self._resolve(f"/{SHARED}/{name}")
                self.assertIsNotNone(found)
                self.assertEqual(found._file.pk, shared.pk)
                member = self._resolve(f"/{SHARED}").get_member(name)
                self.assertEqual(member._file.pk, shared.pk)
        self.assertIsNone(self._resolve(f"/{SHARED}/CAF\u00c9.txt"))

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


class LockIsolationTests(_DavTestCase):
    """A DAV lock binds the people its resource is served to, and nobody else.

    wsgidav files a lock under the address it was taken on, and one address
    is as many files as there are users. Keyed by the address alone, a lock
    one user took stopped everyone else writing their own file of that path.
    """

    def setUp(self):
        super().setUp()
        self.team = Group.objects.create(name="Team")
        self.user.groups.add(self.team)
        self.other.groups.add(self.team)
        self.root = tree.create_folder(self.other, "Team", group=self.team)

    def _as(self, username):
        self.auth = test_webdav._basic_auth_header(username, "pass123")

    def _lock(self, path, depth="0", **headers):
        """LOCK *path*; the status and the token granted, if any."""
        code, response_headers, _ = self._request(
            "LOCK",
            path,
            body=_LOCK_BODY,
            headers={"Content-Type": "application/xml", "Depth": depth, **headers},
        )
        return code, response_headers.get("Lock-Token", "").strip("<>")

    def _discovered_locks(self, path):
        code, _, body = self._request(
            "PROPFIND",
            path,
            body=(
                b'<?xml version="1.0"?><propfind xmlns="DAV:"><prop>'
                b"<lockdiscovery/></prop></propfind>"
            ),
            headers={"Depth": "0", "Content-Type": "application/xml"},
        )
        self.assertEqual(code, 207)
        return body.count(b"activelock>") // 2, body

    def test_a_lock_leaves_another_users_file_of_the_same_path_alone(self):
        code, _ = self._lock(f"{HOME}/report.txt")
        self.assertEqual(code, 201)

        self._as("bob")
        path = f"{HOME}/report.txt"
        self.assertEqual(self._request("PUT", path, body=b"bob's")[0], 201)
        self.assertEqual(self._request("PUT", path, body=b"again")[0], 204)
        code, _, _ = self._request(
            "MOVE", path, headers={"Destination": f"{DAV_HOME}/renamed.txt"}
        )
        self.assertEqual(code, 201)
        self.assertEqual(self._request("DELETE", f"{HOME}/renamed.txt")[0], 204)

    def test_two_users_lock_their_own_file_of_the_same_path(self):
        mine = self._file(self.user, "report.txt", b"mine")
        theirs = self._file(self.other, "report.txt", b"theirs")

        self.assertEqual(self._lock(f"{HOME}/report.txt")[0], 200)
        self._as("bob")
        self.assertEqual(self._lock(f"{HOME}/report.txt")[0], 200)

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.locked_by, self.user)
        self.assertEqual(theirs.locked_by, self.other)

    def test_the_lock_still_binds_its_holders_other_clients(self):
        self._file(self.user, "report.txt", b"mine")
        _, token = self._lock(f"{HOME}/report.txt")

        code, _, _ = self._request("PUT", f"{HOME}/report.txt", body=b"no token")
        self.assertEqual(code, 423)
        code, _, _ = self._request(
            "PUT", f"{HOME}/report.txt", body=b"token", headers={"If": f"(<{token}>)"}
        )
        self.assertEqual(code, 204)

    def test_the_holder_refreshes_and_releases_the_lock(self):
        """wsgidav finds the locked resource back from the key of its lock."""
        mine = self._file(self.user, "report.txt", b"mine")
        _, token = self._lock(f"{HOME}/report.txt")

        code, _, body = self._request(
            "LOCK",
            f"{HOME}/report.txt",
            headers={"If": f"(<{token}>)", "Timeout": "Second-120"},
        )
        self.assertEqual(code, 200)
        self.assertIn(quote(f"{HOME}/report.txt").encode(), body)
        self.assertNotIn(b"~", body)

        code, _, _ = self._request(
            "UNLOCK", f"{HOME}/report.txt", headers={"Lock-Token": f"<{token}>"}
        )
        self.assertEqual(code, 204)
        mine.refresh_from_db()
        self.assertIsNone(mine.locked_by_id)

    def test_a_lock_on_a_folder_of_the_layout_binds_nobody_else(self):
        for folder in ("/", HOME, f"/{GROUPS}", f"/{SHARED}"):
            with self.subTest(folder=folder):
                self._as("alice")
                code, token = self._lock(folder, depth="infinity")
                self.assertIn(code, (200, 201))

                self._as("bob")
                name = f"{uuid.uuid4().hex}.txt"
                self.assertEqual(self._request("PUT", f"{HOME}/{name}")[0], 201)
                self.assertEqual(self._request("PUT", f"/{GROUPS}/Team/{name}")[0], 201)

                self._as("alice")
                code, _, _ = self._request(
                    "UNLOCK", folder, headers={"Lock-Token": f"<{token}>"}
                )
                self.assertEqual(code, 204)

    def test_a_lock_in_a_group_tree_binds_the_other_members(self):
        self._file(self.other, "notes.txt", b"notes", parent=self.root)
        sub = tree.create_folder(self.other, "Sub", parent=self.root)
        self.assertEqual(self._lock(f"/{GROUPS}/Team/notes.txt")[0], 200)
        self.assertEqual(self._lock(f"/{GROUPS}/Team/Sub", depth="infinity")[0], 200)

        self._as("bob")
        code, _, _ = self._request("PUT", f"/{GROUPS}/Team/notes.txt", body=b"mine")
        self.assertEqual(code, 423)
        code, _, _ = self._request("PUT", f"/{GROUPS}/Team/Sub/new.txt", body=b"new")
        self.assertEqual(code, 423)
        # A file someone else holds is refused before the lock table is read
        # (``FileResource.prevent_locking``); a name under the locked folder
        # is refused by the table.
        self.assertEqual(self._lock(f"/{GROUPS}/Team/notes.txt")[0], 403)
        self.assertEqual(self._lock(f"/{GROUPS}/Team/Sub/other.txt")[0], 423)
        self.assertFalse(sub.children.exists())
        # Elsewhere in the group's tree, and in their own, nothing holds them.
        self.assertEqual(self._request("PUT", f"/{GROUPS}/Team/free.txt")[0], 201)
        self.assertEqual(self._request("PUT", f"{HOME}/notes.txt")[0], 201)

    def test_another_users_lock_is_not_disclosed(self):
        _, token = self._lock(f"{HOME}/report.txt")
        count, body = self._discovered_locks(f"{HOME}/report.txt")
        self.assertEqual(count, 1)
        self.assertIn(token.encode(), body)

        self._as("bob")
        self._request("PUT", f"{HOME}/report.txt", body=b"bob's")
        count, body = self._discovered_locks(f"{HOME}/report.txt")

        self.assertEqual(count, 0)
        self.assertNotIn(token.encode(), body)

    def test_a_group_lock_is_disclosed_to_the_other_members(self):
        self._file(self.other, "notes.txt", b"notes", parent=self.root)
        self._lock(f"/{GROUPS}/Team/notes.txt")

        self._as("bob")
        count, _ = self._discovered_locks(f"/{GROUPS}/Team/notes.txt")

        self.assertEqual(count, 1)

    def test_removing_an_own_file_leaves_another_users_lock_in_place(self):
        mine = self._file(self.user, "report.txt", b"mine")
        self._lock(f"{HOME}/report.txt")

        self._as("bob")
        self._request("PUT", f"{HOME}/report.txt", body=b"bob's")
        self._request("PUT", f"{HOME}/other.txt", body=b"bob's")
        self.assertEqual(self._request("DELETE", f"{HOME}/report.txt")[0], 204)
        code, _, _ = self._request(
            "MOVE",
            f"{HOME}/other.txt",
            headers={"Destination": f"{DAV_HOME}/report.txt"},
        )
        self.assertEqual(code, 201)
        self.assertEqual(self._request("DELETE", f"{HOME}/report.txt")[0], 204)

        mine.refresh_from_db()
        self.assertEqual(mine.locked_by, self.user)
        self.assertNotEqual(mine.lock_token, "")
        self._as("alice")
        code, _, _ = self._request("PUT", f"{HOME}/report.txt", body=b"no token")
        self.assertEqual(code, 423)

    def test_another_users_lock_cannot_be_refreshed(self):
        mine = self._file(self.user, "report.txt", b"mine")
        _, token = self._lock(f"{HOME}/report.txt")
        mine.refresh_from_db()
        expires_at = mine.lock_expires_at

        self._as("bob")
        self._request("PUT", f"{HOME}/report.txt", body=b"bob's")
        code, _, _ = self._request(
            "LOCK",
            f"{HOME}/report.txt",
            headers={"If": f"(<{token}>)", "Timeout": "Second-300"},
        )

        self.assertEqual(code, 412)
        mine.refresh_from_db()
        self.assertEqual(mine.lock_expires_at, expires_at)

    def test_both_unicode_spellings_of_an_address_are_one_lock(self):
        """The holder's other client must not get past the lock by spelling
        the address the other way."""
        mine = self._file(self.user, "\u00e9t\u00e9.txt", b"mine")
        composed = f"{HOME}/\u00e9t\u00e9.txt"
        decomposed = unicodedata.normalize("NFD", composed)

        self.assertEqual(
            namespace.lock_key(self.user, composed),
            namespace.lock_key(self.user, decomposed),
        )
        self.assertEqual(
            self._resolve(composed).get_ref_url(),
            self._resolve(decomposed).get_ref_url(),
        )
        code, token = self._lock(_wsgi(composed))
        self.assertEqual(code, 200)

        code, _, _ = self._request("PUT", _wsgi(decomposed), body=b"no token")
        self.assertEqual(code, 423)
        code, _, _ = self._request(
            "PUT", _wsgi(decomposed), body=b"token", headers={"If": f"(<{token}>)"}
        )
        self.assertEqual(code, 204)
        self.assertEqual(self._body(mine), b"token")

    def test_a_name_from_before_names_were_composed_is_locked_either_way(self):
        legacy = self._stored_decomposed(
            self._file(self.user, "\u00e9t\u00e9.txt", b"x")
        )
        composed = f"{HOME}/\u00e9t\u00e9.txt"

        for form in ("NFC", "NFD"):
            with self.subTest(form=form):
                path = _wsgi(unicodedata.normalize(form, composed))
                code, token = self._lock(path)
                self.assertEqual(code, 200)
                legacy.refresh_from_db()
                self.assertEqual(legacy.locked_by, self.user)
                code, _, _ = self._request(
                    "UNLOCK", path, headers={"Lock-Token": f"<{token}>"}
                )
                self.assertEqual(code, 204)
        self.assertEqual(File.objects.filter(owner=self.user).count(), 1)

    def test_a_lock_key_is_the_users_own_outside_a_group_tree(self):
        for path in ("/", f"{HOME}/", f"{HOME}/a b/c.txt", f"/{SHARED}/x.txt"):
            with self.subTest(path=path):
                mine = namespace.lock_key(self.user, path)
                theirs = namespace.lock_key(self.other, path)
                self.assertNotEqual(mine, theirs)
                self.assertEqual(namespace.lock_key_path(mine), path)
                self.assertEqual(namespace.lock_key_path(theirs), path)
        # The folder that lists the groups is each user's own; a group's
        # tree is the same resource for all its members.
        self.assertNotEqual(
            namespace.lock_key(self.user, f"/{GROUPS}/"),
            namespace.lock_key(self.other, f"/{GROUPS}/"),
        )
        for path in (f"/{GROUPS}/Team/", f"/{GROUPS}/Team/a b/c.txt"):
            with self.subTest(path=path):
                key = namespace.lock_key(self.user, path)
                self.assertEqual(key, namespace.lock_key(self.other, path))
                self.assertEqual(namespace.lock_key_path(key), path)

    def test_no_lock_key_of_a_user_sits_under_another_key(self):
        """A lock covers what is under its key: nobody's root is under the
        root of someone else, or of a group's tree."""
        keys = [
            namespace.lock_key(self.user, "/"),
            namespace.lock_key(self.other, "/"),
            namespace.lock_key(self.user, f"/{GROUPS}/Team/"),
        ]
        for key in keys:
            for other in keys:
                if key is not other:
                    self.assertFalse(other.startswith(key), (key, other))
