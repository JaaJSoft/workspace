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

    TEXT_MIMES = {
        'text/plain',
        'text/markdown',
        'text/csv',
        'text/html',
        'text/css',
        'application/json',
        'application/xml',
        'application/javascript',
        'text/x-python',
        'text/x-java',
        'text/x-c',
        'text/x-c++',
        'text/x-sh',
        'text/x-script.python',
        'application/x-python-code',
    }

    IMAGE_MIMES = {
        'image/jpeg',
        'image/png',
        'image/gif',
        'image/webp',
        'image/svg+xml',
        'image/bmp',
        'image/tiff',
        'image/x-icon',
    }

    PDF_MIMES = {
        'application/pdf',
    }

    VIDEO_MIMES = {
        'video/mp4',
        'video/webm',
        'video/ogg',
        'video/quicktime',
        'video/x-msvideo',
        'video/x-matroska',
    }

    AUDIO_MIMES = {
        'audio/mpeg',
        'audio/wav',
        'audio/ogg',
        'audio/webm',
        'audio/aac',
        'audio/mp4',
        'audio/x-m4a',
    }

    @classmethod
    def categorize(cls, mime_type: str) -> FileCategory:
        """Categorize a file by its MIME type.

        Args:
            mime_type: The MIME type string (e.g., 'text/plain')

        Returns:
            FileCategory enum value
        """
        if not mime_type:
            return FileCategory.UNKNOWN

        # Exact match first
        if mime_type in cls.TEXT_MIMES:
            return FileCategory.TEXT
        elif mime_type in cls.IMAGE_MIMES:
            return FileCategory.IMAGE
        elif mime_type in cls.PDF_MIMES:
            return FileCategory.PDF
        elif mime_type in cls.VIDEO_MIMES:
            return FileCategory.VIDEO
        elif mime_type in cls.AUDIO_MIMES:
            return FileCategory.AUDIO

        # Prefix match for text/* (catch-all for text files)
        if mime_type.startswith('text/'):
            return FileCategory.TEXT

        return FileCategory.UNKNOWN

    @classmethod
    def is_viewable(cls, mime_type: str) -> bool:
        """Check if a file can be viewed in the browser.

        Args:
            mime_type: The MIME type string

        Returns:
            True if the file type is supported for viewing
        """
        return cls.categorize(mime_type) != FileCategory.UNKNOWN
