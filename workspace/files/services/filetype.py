"""File type registry - maps content labels to display properties and viewers."""

from dataclasses import dataclass
from typing import Optional, Type

from workspace.files.services.detection import (
    get_all_labels,
    has_extension,
    label_from_name,
)

_KB = get_all_labels()

_GROUP_DEFAULTS = {
    'code':        {'icon': 'file-code',    'color': 'text-info'},
    'text':        {'icon': 'file-text',    'color': 'text-info'},
    'image':       {'icon': 'image',        'color': 'text-success'},
    'video':       {'icon': 'video',        'color': 'text-error'},
    'audio':       {'icon': 'music',        'color': 'text-secondary'},
    'document':    {'icon': 'file-text',    'color': 'text-base-content/60'},
    'archive':     {'icon': 'file-archive', 'color': 'text-warning'},
    'font':        {'icon': 'type',         'color': 'text-base-content/60'},
    'executable':  {'icon': 'binary',       'color': 'text-base-content/60'},
    'application': {'icon': 'file',         'color': 'text-base-content/60'},
}

_LABEL_OVERRIDES = {
    'pdf':         {'color': 'text-error'},
    'csv':         {'icon': 'file-spreadsheet'},
    'tsv':         {'icon': 'file-spreadsheet'},
    'xlsx':        {'icon': 'file-spreadsheet'},
    'xls':         {'icon': 'file-spreadsheet'},
    'ods':         {'icon': 'file-spreadsheet'},
    'pptx':        {'icon': 'file-presentation'},
    'ppt':         {'icon': 'file-presentation'},
    'odp':         {'icon': 'file-presentation'},
    'json':        {'icon': 'file-json'},
    'jsonl':       {'icon': 'file-json'},
    'jsonc':       {'icon': 'file-json'},
    'dockerfile':  {'icon': 'container'},
    'svg':         {'icon': 'image'},
    'epub':        {'icon': 'book-open'},
}

_DEFAULT_ICON = 'file'
_DEFAULT_COLOR = 'text-base-content/60'

# Reverse map: MIME type -> label (for callers that only have a MIME type, e.g. chat attachments)
_MIME_TO_LABEL = {}
for _label, _info in _KB.items():
    _mime = _info.get('mime_type', '')
    if _mime and _mime not in _MIME_TO_LABEL:
        _MIME_TO_LABEL[_mime] = _label


@dataclass(frozen=True)
class FileTypeInfo:
    icon: str
    color: str
    group: str
    viewer: Optional[Type] = None
    mime_type: str = 'application/octet-stream'


def _normalize_group(label: str) -> str:
    info = _KB.get(label, {})
    group = info.get('group') or ''
    if group:
        return group
    if info.get('is_text'):
        return 'code'
    return 'unknown'


def _resolve_viewer(label: str, group: str, ext_label: str = '', ext_group: str = '',
                    file_has_extension: bool = True):
    """Pick the best viewer from the content type and the extension hint.

    Content detection stays primary; the extension only acts as a tiebreaker
    that can upgrade a generic content label (e.g. ``txt``) to a more specific
    viewer (e.g. MarkdownViewer for a ``.md`` file). Priority, strongest first:

      1. content label match   (viewer.handles_labels contains the content label)
      2. extension label match  (handles_labels contains the extension label)
      3. content group match    (viewer.handles_groups contains the content group)
      4. extension group match  (handles_groups contains the extension group)

    Within a tier, the lowest weight wins.

    Viewers declaring ``requires_extension`` are skipped entirely when the file
    has no extension, so content-only detection never routes to them.
    """
    from workspace.files.ui.viewers import BaseViewer

    tiers = ([], [], [], [])
    for viewer_cls in BaseViewer.__subclasses__():
        if not hasattr(viewer_cls, 'handles_labels'):
            continue
        if getattr(viewer_cls, 'requires_extension', False) and not file_has_extension:
            continue
        if label and label in viewer_cls.handles_labels:
            tiers[0].append((viewer_cls.weight, viewer_cls))
        elif ext_label and ext_label in viewer_cls.handles_labels:
            tiers[1].append((viewer_cls.weight, viewer_cls))
        elif group and group in viewer_cls.handles_groups:
            tiers[2].append((viewer_cls.weight, viewer_cls))
        elif ext_group and ext_group in viewer_cls.handles_groups:
            tiers[3].append((viewer_cls.weight, viewer_cls))

    for candidates in tiers:
        if candidates:
            return min(candidates, key=lambda x: x[0])[1]
    return None


def _resolve_label(label_or_mime: str) -> str:
    """Accept either a Magika label or a MIME type string and return the label."""
    if label_or_mime in _KB:
        return label_or_mime
    if '/' in label_or_mime:
        return _MIME_TO_LABEL.get(label_or_mime, label_or_mime)
    return label_or_mime


def get_info(label: str, name: str = '') -> FileTypeInfo:
    """Resolve display properties and viewer for a content label.

    ``name`` is the optional filename; its extension supplements viewer
    resolution so that e.g. a ``.md`` file detected as plain ``txt`` still
    opens in the MarkdownViewer. Icon/color/group stay driven by the
    content label - the extension only influences which viewer handles it.
    """
    if not label and not name:
        return FileTypeInfo(icon=_DEFAULT_ICON, color=_DEFAULT_COLOR, group='unknown')

    label = _resolve_label(label)
    group = _normalize_group(label)
    kb_entry = _KB.get(label, {})
    mime_type = kb_entry.get('mime_type', 'application/octet-stream')

    defaults = _GROUP_DEFAULTS.get(group, {})
    icon = defaults.get('icon', _DEFAULT_ICON)
    color = defaults.get('color', _DEFAULT_COLOR)

    overrides = _LABEL_OVERRIDES.get(label, {})
    icon = overrides.get('icon', icon)
    color = overrides.get('color', color)

    ext_label = ''
    ext_group = ''
    # No name supplied means a label-only caller; don't suppress extension-gated
    # viewers in that case. Only an explicit, extensionless filename does.
    file_has_extension = has_extension(name) if name else True
    if name:
        candidate = label_from_name(name)
        if candidate and candidate != 'unknown' and candidate != label:
            ext_label = candidate
            ext_group = _normalize_group(candidate)

    viewer = _resolve_viewer(label, group, ext_label, ext_group, file_has_extension)

    return FileTypeInfo(icon=icon, color=color, group=group, viewer=viewer, mime_type=mime_type)


def get_icon(label: str) -> str:
    return get_info(label).icon


def get_color(label: str) -> str:
    return get_info(label).color


def get_group(label: str) -> str:
    return get_info(label).group


def get_viewer(label: str, name: str = ''):
    return get_info(label, name).viewer


def get_mime_type(label: str) -> str:
    return get_info(label).mime_type


def is_viewable(label: str, name: str = '') -> bool:
    return get_info(label, name).viewer is not None


def label_from_mime(mime_type: str) -> str:
    """Convert a MIME type to a Magika label. Returns the input unchanged if no match."""
    return _resolve_label(mime_type)
