"""File type registry - maps content labels to display properties and viewers."""

from dataclasses import dataclass
from typing import Optional, Type

from workspace.files.services.detection import get_all_labels

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


def _resolve_viewer(label: str, group: str):
    from workspace.files.ui.viewers import BaseViewer

    label_matches = []
    group_matches = []
    for viewer_cls in BaseViewer.__subclasses__():
        if not hasattr(viewer_cls, 'handles_labels'):
            continue
        if label in viewer_cls.handles_labels:
            label_matches.append((viewer_cls.weight, viewer_cls))
        elif group in viewer_cls.handles_groups:
            group_matches.append((viewer_cls.weight, viewer_cls))

    candidates = label_matches or group_matches
    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])[1]


def _resolve_label(label_or_mime: str) -> str:
    """Accept either a Magika label or a MIME type string and return the label."""
    if label_or_mime in _KB:
        return label_or_mime
    if '/' in label_or_mime:
        return _MIME_TO_LABEL.get(label_or_mime, label_or_mime)
    return label_or_mime


def get_info(label: str) -> FileTypeInfo:
    if not label:
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

    viewer = _resolve_viewer(label, group)

    return FileTypeInfo(icon=icon, color=color, group=group, viewer=viewer, mime_type=mime_type)


def get_icon(label: str) -> str:
    return get_info(label).icon


def get_color(label: str) -> str:
    return get_info(label).color


def get_group(label: str) -> str:
    return get_info(label).group


def get_viewer(label: str):
    return get_info(label).viewer


def get_mime_type(label: str) -> str:
    return get_info(label).mime_type


def is_viewable(label: str) -> bool:
    return get_info(label).viewer is not None
