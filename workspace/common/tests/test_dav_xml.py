from xml.etree import ElementTree as ET

from django.test import SimpleTestCase

from workspace.common.dav.xml import (
    DAV,
    DavError,
    Multistatus,
    element,
    parse_body,
    qname,
    requested_properties,
)


class ParseBodyTests(SimpleTestCase):
    def test_empty_body_is_none(self):
        self.assertIsNone(parse_body(b""))
        self.assertIsNone(parse_body(b"  \n"))

    def test_malformed_body_is_a_400(self):
        with self.assertRaises(DavError) as ctx:
            parse_body(b"<propfind")
        self.assertEqual(ctx.exception.status, 400)

    def test_dtd_is_refused(self):
        body = (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
            b'<propfind xmlns="DAV:">&a;</propfind>'
        )
        with self.assertRaises(DavError) as ctx:
            parse_body(body)
        self.assertEqual(ctx.exception.status, 400)


class RequestedPropertiesTests(SimpleTestCase):
    def test_empty_body_means_allprop(self):
        self.assertEqual(requested_properties(None), ("allprop", []))

    def test_prop_lists_qualified_names(self):
        root = parse_body(
            b'<d:propfind xmlns:d="DAV:" xmlns:x="urn:x">'
            b"<d:prop><d:getetag/><x:color/></d:prop></d:propfind>"
        )
        self.assertEqual(
            requested_properties(root),
            ("prop", [qname(DAV, "getetag"), "{urn:x}color"]),
        )

    def test_propname(self):
        root = parse_body(b'<propfind xmlns="DAV:"><propname/></propfind>')
        self.assertEqual(requested_properties(root), ("propname", []))


class MultistatusTests(SimpleTestCase):
    def test_found_missing_and_bare_status(self):
        ms = Multistatus()
        ms.add_propstat(
            "/dav/a b/",
            found=[element(qname(DAV, "displayname"), "A")],
            missing=["{urn:x}color"],
        )
        ms.add_status("/dav/gone.ics", 404)
        response = ms.response()
        self.assertEqual(response.status_code, 207)
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")

        root = ET.fromstring(response.content)
        first, second = root.findall(qname(DAV, "response"))
        self.assertEqual(first.findtext(qname(DAV, "href")), "/dav/a%20b/")
        statuses = [
            p.findtext(qname(DAV, "status")) for p in first.iter(qname(DAV, "propstat"))
        ]
        self.assertEqual(statuses, ["HTTP/1.1 200 OK", "HTTP/1.1 404 Not Found"])
        self.assertEqual(
            second.findtext(qname(DAV, "status")), "HTTP/1.1 404 Not Found"
        )

    def test_error_names_its_precondition(self):
        response = DavError(403, qname(DAV, "valid-sync-token")).response()
        self.assertEqual(response.status_code, 403)
        root = ET.fromstring(response.content)
        self.assertIsNotNone(root.find(qname(DAV, "valid-sync-token")))
