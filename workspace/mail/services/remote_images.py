"""Whether a sanitized HTML body would fetch an image from the network.

This only decides whether the message offers to show its images. What keeps
them from loading is the Content-Security-Policy of the frame the body is
rendered in, so a URL this scan misses (a CSS escape spelling ``url``) stays
blocked all the same; it just goes without the button.
"""

import re
from html.parser import HTMLParser

# Where a style names an image: every url(), and every quoted string of an
# image-set(), whose candidates are URLs with or without url(). Background
# images, list markers and border images all go through one of them.
_CSS_URL = re.compile(r"url\(\s*['\"]?\s*([^)'\"\s]*)", re.IGNORECASE)
_IMAGE_SET = re.compile(r"image-set\(", re.IGNORECASE)
# type("image/avif") qualifies a candidate; its string is a media type, not a URL.
_CSS_TYPE = re.compile(r"type\(\s*['\"][^'\"]*['\"]\s*\)", re.IGNORECASE)
_CSS_STRING = re.compile(r"['\"]\s*([^'\"]*?)\s*['\"]")


def _arguments(style, start):
    """The text inside the parenthesis opened just before ``start``, and where
    it ends."""
    depth = 1
    for index in range(start, len(style)):
        if style[index] == "(":
            depth += 1
        elif style[index] == ")":
            depth -= 1
            if depth == 0:
                return style[start:index], index
    return style[start:], len(style)


def _css_image_urls(style):
    yield from _CSS_URL.findall(style)
    scanned = 0
    for match in _IMAGE_SET.finditer(style):
        # An image-set() inside one already read was read with it. Skipping it
        # keeps the scan linear: unclosed ones would otherwise each run to the
        # end of the style.
        if match.start() < scanned:
            continue
        arguments, scanned = _arguments(style, match.end())
        yield from _CSS_STRING.findall(_CSS_TYPE.sub("", arguments))


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
            elif name == "style" and any(
                url and not _is_inline(url) for url in _css_image_urls(value)
            ):
                self.found = True

    handle_startendtag = handle_starttag


def _is_inline(url):
    return url.strip().lower().startswith(("data:", "cid:"))


def has_remote_images(html):
    """True when rendering ``html`` would load at least one remote image."""
    if not html:
        return False
    finder = _RemoteImageFinder()
    finder.feed(html)
    finder.close()
    return finder.found
