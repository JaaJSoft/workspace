"""Utility functions for file management."""

from enum import Enum


class FileCategory(Enum):
    """File category based on MIME type."""
    TEXT = 'text'
    IMAGE = 'image'
    PDF = 'pdf'
    VIDEO = 'video'
    AUDIO = 'audio'
    UNKNOWN = 'unknown'


class FileTypeDetector:
    """File type detection and categorization based on MIME type."""

    _CATEGORY_MAP = {v.value: v for v in FileCategory}

    @classmethod
    def categorize(cls, mime_type: str) -> FileCategory:
        """Categorize a file by its MIME type."""
        from .services.mime import get_category
        return cls._CATEGORY_MAP.get(get_category(mime_type), FileCategory.UNKNOWN)

    @classmethod
    def is_viewable(cls, mime_type: str) -> bool:
        """Check if a file can be viewed in the browser."""
        from .services.mime import is_viewable
        return is_viewable(mime_type)
