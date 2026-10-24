"""Building the property answers of a DAV resource.

``PropertySet`` answers a ``PROPFIND`` (or the ``prop`` of a ``REPORT``) for
one resource; the helpers build the WebDAV and ACL properties every
collection-serving protocol repeats.
"""

from xml.etree import ElementTree as ET

from .xml import DAV, DavError, element, href_element, qname, status_line


def _d(name):
    return qname(DAV, name)


class PropertySet:
    """The properties of one resource, built only when asked for.

    *builders* maps a qualified name to a callable returning the property
    element; *expensive* names stay out of an ``allprop`` answer (the object
    data of CalDAV and CardDAV is excluded from it by their RFCs).
    """

    def __init__(self, builders, expensive=()):
        self.builders = builders
        self.expensive = set(expensive)

    def add_to(self, multistatus, path, mode, requested):
        if mode == "propname":
            multistatus.add_propstat(
                path, found=[element(tag) for tag in self.builders]
            )
            return
        if mode == "allprop":
            requested = [tag for tag in self.builders if tag not in self.expensive]
        found, missing = [], []
        for tag in requested:
            builder = self.builders.get(tag)
            value = builder() if builder else None
            if value is None:
                missing.append(tag)
            else:
                found.append(value)
        multistatus.add_propstat(path, found=found, missing=missing)


def hrefs(tag, *paths):
    """A property holding one ``<href>`` per path."""
    return element(tag, children=[href_element(path) for path in paths])


def resourcetype(*kinds):
    return element(_d("resourcetype"), children=[element(kind) for kind in kinds])


def privilege_set(writable):
    """``current-user-privilege-set``: read, plus every write privilege when writable."""
    names = [_d("read")]
    if writable:
        names += [_d("write"), _d("write-content"), _d("bind"), _d("unbind")]
    privileges = [element(_d("privilege"), children=[element(name)]) for name in names]
    return element(_d("current-user-privilege-set"), children=privileges)


def supported_report_set(*reports):
    return element(
        _d("supported-report-set"),
        children=[
            element(
                _d("supported-report"),
                children=[element(_d("report"), children=[element(report)])],
            )
            for report in reports
        ],
    )


def failed_dependency(response, tags):
    """Add a 424 propstat for *tags* to a ``<response>`` of a refused PROPPATCH."""
    propstat = ET.SubElement(response, _d("propstat"))
    ET.SubElement(propstat, _d("prop")).extend(ET.Element(tag) for tag in tags)
    ET.SubElement(propstat, _d("status")).text = status_line(424)


def parse_sync_token(token, prefix, current_revision):
    """The revision a client's sync token names; 0 for an initial sync.

    A token this server never issued - another prefix, or a revision ahead
    of *current_revision* - is the ``valid-sync-token`` precondition failing.
    """
    if not token:
        return 0
    revision = token.removeprefix(prefix)
    if revision == token or not revision.isdigit() or int(revision) > current_revision:
        raise DavError(403, _d("valid-sync-token"))
    return int(revision)
