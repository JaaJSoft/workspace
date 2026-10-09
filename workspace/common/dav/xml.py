"""WebDAV (RFC 4918) XML plumbing for server-side views.

Parses request bodies and builds ``multistatus`` answers. Protocol extensions
(CalDAV, CardDAV, sync-collection) bring their own namespaces and register a
prefix with ``register_prefix`` so responses stay readable.
"""

from http import HTTPStatus
from urllib.parse import quote
from xml.etree import ElementTree as ET

from django.http import HttpResponse

DAV = "DAV:"

XML_CONTENT_TYPE = "application/xml; charset=utf-8"

ET.register_namespace("d", DAV)


def register_prefix(prefix, namespace):
    """Serialize *namespace* under *prefix* (process-wide, idempotent)."""
    ET.register_namespace(prefix, namespace)


def qname(namespace, name):
    """``{namespace}name``, the ElementTree spelling of a qualified name."""
    return f"{{{namespace}}}{name}"


def status_line(code):
    return f"HTTP/1.1 {code} {HTTPStatus(code).phrase}"


def href(path):
    """Percent-encode a path for an ``<href>``, keeping the slashes."""
    return quote(path, safe="/")


class DavError(Exception):
    """A request the view refuses, optionally naming the failed precondition.

    *condition* is the qualified name of the RFC precondition element
    (``{DAV:}valid-sync-token``); the answer carries it inside ``<d:error>``
    so a client can tell "your token is stale" from a generic 403.
    """

    def __init__(self, status, condition=None, message=""):
        super().__init__(message or HTTPStatus(status).phrase)
        self.status = status
        self.condition = condition

    def response(self):
        if self.condition is None:
            return HttpResponse(
                str(self), status=self.status, content_type="text/plain"
            )
        root = ET.Element(qname(DAV, "error"))
        ET.SubElement(root, self.condition)
        return xml_response(root, status=self.status)


def parse_body(body):
    """Parse a request body into its root element; None when the body is empty.

    A DTD is refused outright: no DAV request needs one, and it is the door to
    entity-expansion attacks.
    """
    if not body or not body.strip():
        return None
    if b"<!DOCTYPE" in body[:1024].upper() or b"<!ENTITY" in body.upper():
        raise DavError(400, message="DTDs are not accepted.")
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise DavError(400, message="Malformed XML body.") from exc


def requested_properties(root):
    """Read a ``propfind`` body as ``("allprop" | "propname" | "prop", tags)``.

    An empty body is an ``allprop`` request (RFC 4918 9.1). *tags* lists the
    qualified names asked for, and is empty unless the mode is ``prop``.
    """
    if root is None:
        return "allprop", []
    if root.find(qname(DAV, "propname")) is not None:
        return "propname", []
    prop = root.find(qname(DAV, "prop"))
    if prop is not None:
        return "prop", [child.tag for child in prop]
    return "allprop", []


def xml_response(root, status=200, headers=None):
    body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    response = HttpResponse(body, status=status, content_type=XML_CONTENT_TYPE)
    for name, value in (headers or {}).items():
        response[name] = value
    return response


class Multistatus:
    """Builder for a ``207 Multi-Status`` answer."""

    def __init__(self):
        self.root = ET.Element(qname(DAV, "multistatus"))

    def add_propstat(self, path, found=(), missing=(), forbidden=()):
        """One ``<response>`` for *path*.

        *found* holds the property elements to return with 200, *missing* and
        *forbidden* the qualified names answered with 404 and 403.
        """
        response = self._response(path)
        if found:
            self._propstat(response, 200, found)
        if missing:
            self._propstat(response, 404, [ET.Element(tag) for tag in missing])
        if forbidden:
            self._propstat(response, 403, [ET.Element(tag) for tag in forbidden])
        return response

    def add_status(self, path, status):
        """A ``<response>`` carrying a bare status (a deleted member, a 404)."""
        response = self._response(path)
        ET.SubElement(response, qname(DAV, "status")).text = status_line(status)
        return response

    def append(self, element):
        self.root.append(element)

    def response(self):
        return xml_response(self.root, status=207)

    def _response(self, path):
        response = ET.SubElement(self.root, qname(DAV, "response"))
        ET.SubElement(response, qname(DAV, "href")).text = href(path)
        return response

    @staticmethod
    def _propstat(response, status, elements):
        propstat = ET.SubElement(response, qname(DAV, "propstat"))
        prop = ET.SubElement(propstat, qname(DAV, "prop"))
        prop.extend(elements)
        ET.SubElement(propstat, qname(DAV, "status")).text = status_line(status)


def element(tag, text=None, children=()):
    """Build an element in one expression: ``element(tag, "text")``."""
    node = ET.Element(tag)
    if text is not None:
        node.text = str(text)
    node.extend(children)
    return node


def href_element(path):
    return element(qname(DAV, "href"), href(path))
