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

    def test_sidebar_partial_carries_the_swap_target_id(self):
        # The sidebar refresh swaps this response into the page via
        # alpine-ajax, which removes the target element outright when an OK
        # response lacks the matching id - so the partial must render the
        # #notes-sidebar wrapper itself.
        resp = self.client.get("/notes", headers={"X-Alpine-Request": "true"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="notes-sidebar"')

    def test_full_page_renders_the_sidebar_wrapper_once(self):
        # The wrapper lives in the partial; a second copy in the parent
        # template would produce duplicate ids and break the swap targeting.
        resp = self.client.get("/notes")
        self.assertEqual(resp.content.decode().count('id="notes-sidebar"'), 1)
