from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class DrawerOrderTests(TestCase):
    """The drawer sidebar is sent before the drawer content.

    The browser paints a long document as it parses it. With the sidebar
    after the content, a large listing painted at full width for as long as
    its parse took, then shifted right once the aside arrived. daisyUI places
    both by grid column, so the order in the markup is free to follow the
    paint order.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="drawer", email="drawer@example.com", password="x"
        )
        self.client.force_login(self.user)

    def test_sidebar_precedes_content_in_every_module_shell(self):
        for path in ("/files", "/chat", "/notes", "/mail", "/calendar", "/projects"):
            with self.subTest(path=path):
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                side = html.find('class="drawer-side')
                content = html.find('class="drawer-content')
                self.assertNotEqual(side, -1, f"{path} has no drawer sidebar")
                self.assertNotEqual(content, -1, f"{path} has no drawer content")
                self.assertLess(
                    side, content, f"{path} sends the content before the sidebar"
                )
