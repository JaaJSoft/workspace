"""The Document and Audio sections of the Files properties panel.

Registered from ``FilesConfig.ready()`` through ``files.properties_sections``,
like the ones other modules contribute. A video's section belongs to photos,
which knows its dimensions.
"""

from ..models import DocumentInfo, MediaInfo
from ..properties_sections import PropertiesSection, properties_section_registry
from .document_info import DOCUMENT_LABELS
from .media_info import AUDIO_LABELS


def current_document_info(file_obj):
    """*file_obj*'s DocumentInfo row when it describes its current content, or None.

    A row describing replaced bytes is not shown: until the catch-up reads the
    file again, its title and author are those of another document.
    """
    if file_obj.type not in DOCUMENT_LABELS:
        return None
    try:
        info = file_obj.document_info
    except DocumentInfo.DoesNotExist:
        return None
    return info if info.content_hash == file_obj.content_hash else None


def current_media_info(file_obj):
    """*file_obj*'s MediaInfo row when it describes its current content, or None."""
    try:
        info = file_obj.media_info
    except MediaInfo.DoesNotExist:
        return None
    return info if info.content_hash == file_obj.content_hash else None


def _has_document_details(info):
    return bool(
        info.title
        or info.author
        or info.subject
        or info.keywords
        or info.page_count
        or info.created
        or info.modified
    )


def is_document_section_visible(user, file_obj):
    info = current_document_info(file_obj)
    return info is not None and _has_document_details(info)


def document_section_context(user, file_obj):
    return {"document": current_document_info(file_obj)}


def is_audio_section_visible(user, file_obj):
    if file_obj.type not in AUDIO_LABELS:
        return False
    info = current_media_info(file_obj)
    return info is not None and bool(info.duration or info.audio_codec)


def audio_section_context(user, file_obj):
    return {"media": current_media_info(file_obj)}


def register_properties_sections():
    properties_section_registry.register(
        PropertiesSection(
            slug="document",
            label="Document",
            template="files/ui/partials/properties_document_section.html",
            is_visible=is_document_section_visible,
            get_context=document_section_context,
        )
    )
    properties_section_registry.register(
        PropertiesSection(
            slug="audio",
            label="Audio",
            template="files/ui/partials/properties_audio_section.html",
            is_visible=is_audio_section_visible,
            get_context=audio_section_context,
        )
    )
