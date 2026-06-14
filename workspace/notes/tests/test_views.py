from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class NotesIndexViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="iv", password="p")
        self.client.force_login(self.user)

    def test_graph_view_is_preserved(self):
        # Reloading (F5) on the graph view must restore it, not fall back to
        # "My Notes" - so the index view must accept view=graph.
        resp = self.client.get("/notes?view=graph")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["initial_view"], "graph")

    def test_unknown_view_falls_back_to_all(self):
        resp = self.client.get("/notes?view=bogus")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["initial_view"], "all")
