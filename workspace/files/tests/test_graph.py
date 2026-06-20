from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileFavorite, FileLink, FileTag, Tag
from workspace.files.services.graph import build_file_graph

User = get_user_model()


def _note(owner, name, *, parent=None, group=None, type="markdown", deleted=False):
    f = File.objects.create(
        owner=owner,
        name=name,
        node_type=File.NodeType.FILE,
        parent=parent,
        group=group,
        type=type,
    )
    if deleted:
        f.deleted_at = f.created_at
        f.save(update_fields=["deleted_at"])
    return f


class BuildFileGraphTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="g", password="p")
        cls.other = User.objects.create_user(username="o", password="p")
        # Own notes: a -> b ; c is an orphan ; t is a non-markdown file
        cls.a = _note(cls.user, "a.md")
        cls.b = _note(cls.user, "b.md")
        cls.c = _note(cls.user, "c.md")
        cls.t = _note(cls.user, "n.txt", type="text")
        FileLink.objects.create(source=cls.a, target=cls.b)
        # A link to a note owned by someone else (out of "mine" scope)
        cls.x = _note(cls.other, "x.md")
        FileLink.objects.create(source=cls.a, target=cls.x)
        FileFavorite.objects.create(owner=cls.user, file=cls.b)

    def test_mine_scope_nodes_and_edges(self):
        g = build_file_graph(self.user, scope="mine", file_type="markdown")
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.b.uuid), str(self.c.uuid)})
        # Orphan c is included; non-markdown n.txt excluded.
        self.assertEqual(
            g["edges"], [{"source": str(self.a.uuid), "target": str(self.b.uuid)}]
        )
        # The a->x edge is dropped: x is out of scope.

    def test_favorite_and_parent_flags(self):
        g = build_file_graph(self.user, scope="mine", file_type="markdown")
        by_id = {n["uuid"]: n for n in g["nodes"]}
        self.assertTrue(by_id[str(self.b.uuid)]["is_favorite"])
        self.assertFalse(by_id[str(self.a.uuid)]["is_favorite"])
        self.assertIsNone(by_id[str(self.a.uuid)]["parent"])

    def test_type_filter_optional(self):
        g = build_file_graph(self.user, scope="mine", file_type=None)
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertIn(str(self.t.uuid), ids)  # no type filter -> txt included

    def test_all_scope_includes_shared(self):
        # Share other's note x with user -> appears in "all", and the a->x edge resolves.
        from workspace.files.models import FileShare

        FileShare.objects.create(
            file=self.x,
            shared_by=self.other,
            shared_with=self.user,
            permission=FileShare.Permission.READ_ONLY,
        )
        g = build_file_graph(self.user, scope="all", file_type="markdown")
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertIn(str(self.x.uuid), ids)
        self.assertIn(
            {"source": str(self.a.uuid), "target": str(self.x.uuid)}, g["edges"]
        )

    def test_all_scope_no_duplicate_nodes_when_owned_and_shared(self):
        # accessible_files_q ORs owner with a join to shares; an owned file with
        # multiple share rows would fan out to duplicate nodes without distinct().
        from workspace.files.models import FileShare

        u2 = User.objects.create_user(username="u2", password="p")
        u3 = User.objects.create_user(username="u3", password="p")
        FileShare.objects.create(
            file=self.a,
            shared_by=self.user,
            shared_with=u2,
            permission=FileShare.Permission.READ_ONLY,
        )
        FileShare.objects.create(
            file=self.a,
            shared_by=self.user,
            shared_with=u3,
            permission=FileShare.Permission.READ_ONLY,
        )
        g = build_file_graph(self.user, scope="all", file_type="markdown")
        ids = [n["uuid"] for n in g["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count(str(self.a.uuid)), 1)

    def test_deleted_excluded(self):
        d = _note(self.user, "d.md", deleted=True)
        FileLink.objects.create(source=self.a, target=d)
        g = build_file_graph(self.user, scope="mine", file_type="markdown")
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertNotIn(str(d.uuid), ids)
        # No edge to the deleted node.
        self.assertNotIn(
            {"source": str(self.a.uuid), "target": str(d.uuid)}, g["edges"]
        )

    def test_under_scopes_to_folder_subtree(self):
        # "My notes" = the Notes folder subtree, not every owned note.
        folder = File.objects.create(
            owner=self.user, name="NotesRoot", node_type=File.NodeType.FOLDER
        )
        sub = File.objects.create(
            owner=self.user, name="Sub", node_type=File.NodeType.FOLDER, parent=folder
        )
        inside = _note(self.user, "inside.md", parent=folder)
        nested = _note(self.user, "nested.md", parent=sub)
        g = build_file_graph(
            self.user, scope="mine", file_type="markdown", under=folder.uuid
        )
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(inside.uuid), str(nested.uuid)})
        self.assertNotIn(str(self.a.uuid), ids)  # root-level note, outside the folder

    def test_under_unresolvable_returns_empty(self):
        import uuid as uuidlib

        g = build_file_graph(
            self.user, scope="mine", file_type="markdown", under=uuidlib.uuid4()
        )
        self.assertEqual(g["nodes"], [])

    def test_search_filters_nodes_by_name(self):
        # search keeps only name-matching nodes; edges drop to the surviving set.
        g = build_file_graph(self.user, scope="mine", file_type="markdown", search="b")
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(self.b.uuid)})  # only b.md matches "b"
        self.assertEqual(g["edges"], [])  # a->b dropped: a filtered out

    def test_favorites_true_keeps_only_favorites(self):
        g = build_file_graph(
            self.user, scope="mine", file_type="markdown", favorites=True
        )
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(self.b.uuid)})  # only b is favorited

    def test_favorites_false_excludes_favorites(self):
        g = build_file_graph(
            self.user, scope="mine", file_type="markdown", favorites=False
        )
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.c.uuid)})  # b dropped

    def test_exclude_descendants_of_drops_subtree(self):
        folder = File.objects.create(
            owner=self.user, name="Excl", node_type=File.NodeType.FOLDER
        )
        inside = _note(self.user, "inside.md", parent=folder)
        g = build_file_graph(
            self.user,
            scope="mine",
            file_type="markdown",
            exclude_descendants_of=folder.uuid,
        )
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertNotIn(str(inside.uuid), ids)  # subtree dropped
        self.assertIn(str(self.a.uuid), ids)  # root-level note kept

    def test_tags_filter_keeps_only_tagged_nodes(self):
        # A single-tag filter keeps only nodes carrying that tag; edges drop to
        # the surviving set (a->b is gone because a is filtered out).
        tag = Tag.objects.create(owner=self.user, name="work")
        FileTag.objects.create(file=self.b, tag=tag)
        g = build_file_graph(
            self.user, scope="mine", file_type="markdown", tags=[tag.uuid]
        )
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(self.b.uuid)})
        self.assertEqual(g["edges"], [])

    def test_tags_filter_is_or_across_tags(self):
        # Multiple tags use OR semantics: a node matches if it carries any of them.
        t1 = Tag.objects.create(owner=self.user, name="t1")
        t2 = Tag.objects.create(owner=self.user, name="t2")
        FileTag.objects.create(file=self.a, tag=t1)
        FileTag.objects.create(file=self.c, tag=t2)
        g = build_file_graph(
            self.user, scope="mine", file_type="markdown", tags=[t1.uuid, t2.uuid]
        )
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.c.uuid)})

    def test_tags_filter_no_duplicate_when_node_has_several_selected_tags(self):
        # A node carrying several of the selected tags fans out one row per tag;
        # distinct() must collapse it back to a single node.
        t1 = Tag.objects.create(owner=self.user, name="t1")
        t2 = Tag.objects.create(owner=self.user, name="t2")
        FileTag.objects.create(file=self.a, tag=t1)
        FileTag.objects.create(file=self.a, tag=t2)
        g = build_file_graph(
            self.user, scope="mine", file_type="markdown", tags=[t1.uuid, t2.uuid]
        )
        ids = [n["uuid"] for n in g["nodes"]]
        self.assertEqual(ids.count(str(self.a.uuid)), 1)

    def test_tags_none_applies_no_filter(self):
        tag = Tag.objects.create(owner=self.user, name="work")
        FileTag.objects.create(file=self.b, tag=tag)
        g = build_file_graph(self.user, scope="mine", file_type="markdown", tags=None)
        ids = {n["uuid"] for n in g["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.b.uuid), str(self.c.uuid)})

    def test_nodes_are_file_serializer_dto(self):
        # A node is the canonical FileSerializer DTO, not a hand-rolled subset:
        # keyed by "uuid" (no synthetic "id") and carrying the full surface.
        g = build_file_graph(self.user, scope="mine", file_type="markdown")
        node = next(n for n in g["nodes"] if n["uuid"] == str(self.a.uuid))
        self.assertNotIn("id", node)
        expected = {
            "uuid",
            "name",
            "node_type",
            "parent",
            "type",
            "icon",
            "color",
            "owner",
            "size",
            "mime_type",
            "path",
            "category",
            "is_favorite",
            "is_file",
            "is_folder",
            "is_viewable",
            "content_url",
            "is_pinned",
            "is_shared",
            "tags",
            "has_children",
        }
        self.assertTrue(expected.issubset(node.keys()))


class FileGraphEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ge", password="p")
        self.client.force_authenticate(user=self.user)
        self.a = _note(self.user, "a.md")
        self.b = _note(self.user, "b.md")
        FileLink.objects.create(source=self.a, target=self.b)

    def test_graph_returns_nodes_and_edges(self):
        resp = self.client.get("/api/v1/files/graph?scope=mine&type=markdown")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.b.uuid)})
        self.assertEqual(
            resp.data["edges"],
            [{"source": str(self.a.uuid), "target": str(self.b.uuid)}],
        )

    def test_default_scope_is_mine(self):
        resp = self.client.get("/api/v1/files/graph?type=markdown")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_invalid_scope_400(self):
        resp = self.client.get("/api/v1/files/graph?scope=bogus")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_auth(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/files/graph")
        self.assertIn(resp.status_code, (401, 403))

    def test_under_param_scopes_subtree(self):
        folder = File.objects.create(
            owner=self.user, name="F", node_type=File.NodeType.FOLDER
        )
        inside = _note(self.user, "inside.md", parent=folder)
        resp = self.client.get(f"/api/v1/files/graph?type=markdown&under={folder.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(inside.uuid)})

    def test_invalid_under_400(self):
        resp = self.client.get("/api/v1/files/graph?under=bogus")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_search_param_filters(self):
        resp = self.client.get("/api/v1/files/graph?type=markdown&search=b")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(self.b.uuid)})

    def test_favorites_param(self):
        FileFavorite.objects.create(owner=self.user, file=self.a)
        resp = self.client.get("/api/v1/files/graph?type=markdown&favorites=1")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid)})

    def test_empty_favorites_is_unset_not_false(self):
        # `?favorites=` (empty) must mean "no filter", not "non-favorites only":
        # the favorited node must still be returned alongside the rest.
        FileFavorite.objects.create(owner=self.user, file=self.a)
        resp = self.client.get("/api/v1/files/graph?type=markdown&favorites=")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.b.uuid)})

    def test_invalid_exclude_descendants_of_400(self):
        resp = self.client.get("/api/v1/files/graph?exclude_descendants_of=bogus")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tags_param_filters(self):
        tag = Tag.objects.create(owner=self.user, name="work")
        FileTag.objects.create(file=self.a, tag=tag)
        resp = self.client.get(f"/api/v1/files/graph?type=markdown&tags={tag.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid)})

    def test_tags_param_multiple_comma_separated(self):
        t1 = Tag.objects.create(owner=self.user, name="t1")
        t2 = Tag.objects.create(owner=self.user, name="t2")
        FileTag.objects.create(file=self.a, tag=t1)
        FileTag.objects.create(file=self.b, tag=t2)
        resp = self.client.get(
            f"/api/v1/files/graph?type=markdown&tags={t1.uuid},{t2.uuid}"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.b.uuid)})

    def test_invalid_tags_400(self):
        resp = self.client.get("/api/v1/files/graph?tags=bogus")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_tags_is_unset(self):
        # `?tags=` (empty) means "no filter": all nodes returned.
        resp = self.client.get("/api/v1/files/graph?type=markdown&tags=")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {n["uuid"] for n in resp.data["nodes"]}
        self.assertEqual(ids, {str(self.a.uuid), str(self.b.uuid)})
