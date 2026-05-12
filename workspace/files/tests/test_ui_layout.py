from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class PropertiesSidebarLayoutTests(TestCase):
    """The Properties sidebar must overlay (not push) content on mobile.

    Regression: on mobile the sidebar was inline in the flex row at a fixed
    320px width, which squeezed the folder-browser column to a few dozen
    pixels and let the "+" dropdown (flex-shrink-0) overflow visually over
    the sidebar.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='ui-user', email='ui@example.com', password='pw'
        )
        self.client.force_login(self.user)

    def test_properties_sidebar_overlays_on_mobile(self):
        html = self.client.get(reverse('files_ui:index')).content.decode()

        self.assertIn('max-md:absolute', html)
        self.assertIn('max-md:inset-y-0', html)
        self.assertIn('max-md:right-0', html)
        self.assertIn('max-md:z-30', html)
        self.assertIn('md:hidden absolute inset-0 bg-black/40', html)
