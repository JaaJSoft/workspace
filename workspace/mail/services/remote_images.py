"""Whether a sanitized HTML body would fetch an image from the network.

This only decides whether the message offers to show its images. What keeps
them from loading is the Content-Security-Policy of the frame the body is
rendered in, so a URL this scan misses (a CSS escape spelling ``url``) stays
blocked all the same; it just goes without the button.
"""

import re
from html.parser import HTMLParser

# A CSS url() or image-set() whose target is not inline data: background
# images, list markers and border images all go through one of them.
_REMOTE_CSS_URL = re.compile(
    r"(?:url|image-set)\(\s*['\"]?\s*(?!data:)[^)\s'\"]", re.IGNORECASE
)


class _RemoteImageFinder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = False

    def handle_starttag(self, tag, attrs):
        if self.found:
            return
        for name, value in attrs:
            if not value:
                continue
            if name == "src" and tag == "img" and not _is_inline(value):
                self.found = True
            elif name == "style" and _REMOTE_CSS_URL.search(value):
                self.found = True

    handle_startendtag = handle_starttag


def _is_inline(url):
    return url.strip().lower().startswith("data:")


def has_remote_images(html):
    """True when rendering ``html`` would load at least one remote image."""
    if not html:
        return False
    finder = _RemoteImageFinder()
    finder.feed(html)
    finder.close()
    return finder.found
