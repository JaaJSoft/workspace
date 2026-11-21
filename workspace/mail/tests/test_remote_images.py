from django.test import SimpleTestCase

from workspace.mail.services.remote_images import has_remote_images


class HasRemoteImagesTests(SimpleTestCase):
    def test_a_remote_img_counts(self):
        self.assertTrue(
            has_remote_images('<p>Hi</p><img src="https://t.example/p.gif">')
        )

    def test_a_protocol_relative_img_counts(self):
        self.assertTrue(has_remote_images('<img src="//t.example/p.gif">'))

    def test_an_inline_data_img_does_not(self):
        self.assertFalse(has_remote_images('<img src="data:image/png;base64,AAAA">'))

    def test_a_css_background_counts(self):
        self.assertTrue(
            has_remote_images(
                "<td style=\"background: URL( 'https://t.example/bg.png' )\">x</td>"
            )
        )

    def test_an_image_set_counts(self):
        self.assertTrue(
            has_remote_images(
                '<div style="background-image: image-set(&quot;https://t.example/a.png&quot; 1x)"></div>'
            )
        )

    def test_a_css_data_url_does_not(self):
        self.assertFalse(
            has_remote_images(
                '<div style="background: url(data:image/png;base64,AA)"></div>'
            )
        )

    def test_text_mentioning_an_image_does_not(self):
        self.assertFalse(
            has_remote_images("<p>see url(https://x) and &lt;img src=x&gt;</p>")
        )

    def test_an_img_without_src_does_not(self):
        self.assertFalse(has_remote_images('<img alt="gone">'))

    def test_an_empty_body_does_not(self):
        self.assertFalse(has_remote_images(""))
