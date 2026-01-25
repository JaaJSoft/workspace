"""
File viewers system - Server-side rendering of file viewers.

Each viewer class is responsible for generating the HTML to display
a specific type of file in the browser.
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional, Type, List


class ViewerRegistry:
    """Registry for file viewers based on MIME types."""

    _viewers: Dict[str, Type['BaseViewer']] = {}

    @classmethod
    def register(cls, mime_types: List[str], viewer_class: Type['BaseViewer']):
        """Register a viewer for specific MIME types."""
        for mime_type in mime_types:
            cls._viewers[mime_type] = viewer_class

    @classmethod
    def get_viewer(cls, mime_type: str) -> Optional[Type['BaseViewer']]:
        """Get appropriate viewer for a MIME type."""
        if not mime_type:
            return None

        # Exact match first
        if mime_type in cls._viewers:
            return cls._viewers[mime_type]

        # Prefix match (e.g., text/*)
        prefix = mime_type.split('/')[0]
        wildcard_key = f'{prefix}/*'
        if wildcard_key in cls._viewers:
            return cls._viewers[wildcard_key]

        return None

    @classmethod
    def is_supported(cls, mime_type: str) -> bool:
        """Check if a MIME type is supported."""
        return cls.get_viewer(mime_type) is not None


class BaseViewer(ABC):
    """Base class for all file viewers."""

    def __init__(self, file_obj):
        """
        Initialize viewer with file object.

        Args:
            file_obj: File model instance
        """
        self.file = file_obj

    @abstractmethod
    def render(self, request) -> str:
        """
        Render the viewer HTML.

        Args:
            request: Django request object

        Returns:
            HTML string for the viewer
        """
        pass

    def can_edit(self) -> bool:
        """Check if this viewer supports editing."""
        return False

    def get_context(self, request) -> dict:
        """Get context data for template rendering."""
        return {
            'file': self.file,
            'can_edit': self.can_edit(),
        }


class TextViewer(BaseViewer):
    """Viewer for text and code files with Monaco Editor."""

    def render(self, request) -> str:
        """Render Monaco Editor for text files."""
        from django.template.loader import render_to_string

        # Read file content
        try:
            content = self.file.content.read().decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            content = ''

        context = self.get_context(request)
        context.update({
            'language': self._detect_language(),
            'content': content,
        })

        return render_to_string('files/ui/viewers/text_viewer.html', context, request=request)

    def can_edit(self) -> bool:
        """Text files can be edited."""
        return True

    def _detect_language(self) -> str:
        """Detect programming language from file extension."""
        ext = self.file.name.split('.')[-1].lower() if '.' in self.file.name else ''

        lang_map = {
            'js': 'javascript',
            'jsx': 'javascript',
            'ts': 'typescript',
            'tsx': 'typescript',
            'py': 'python',
            'html': 'html',
            'htm': 'html',
            'css': 'css',
            'scss': 'scss',
            'json': 'json',
            'md': 'markdown',
            'xml': 'xml',
            'yaml': 'yaml',
            'yml': 'yaml',
            'sh': 'shell',
            'bash': 'shell',
            'sql': 'sql',
            'php': 'php',
            'java': 'java',
            'c': 'c',
            'cpp': 'cpp',
            'go': 'go',
            'rs': 'rust',
            'rb': 'ruby',
        }

        return lang_map.get(ext, 'plaintext')


class ImageViewer(BaseViewer):
    """Viewer for image files."""

    def render(self, request) -> str:
        """Render image viewer with zoom/rotate controls."""
        from django.template.loader import render_to_string

        return render_to_string('files/ui/viewers/image_viewer.html', self.get_context(request), request=request)


class PDFViewer(BaseViewer):
    """Viewer for PDF files."""

    def render(self, request) -> str:
        """Render PDF viewer."""
        from django.template.loader import render_to_string

        return render_to_string('files/ui/viewers/pdf_viewer.html', self.get_context(request), request=request)


class MediaViewer(BaseViewer):
    """Viewer for video and audio files."""

    def render(self, request) -> str:
        """Render media player."""
        from django.template.loader import render_to_string

        context = self.get_context(request)
        context['is_video'] = self.file.mime_type.startswith('video/')
        context['is_audio'] = self.file.mime_type.startswith('audio/')

        return render_to_string('files/ui/viewers/media_viewer.html', context, request=request)


# Register viewers
ViewerRegistry.register([
    'text/plain',
    'text/markdown',
    'text/csv',
    'text/html',
    'text/css',
    'application/json',
    'application/xml',
    'application/javascript',
    'text/x-python',
    'text/*',
], TextViewer)

ViewerRegistry.register([
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp',
    'image/svg+xml',
    'image/bmp',
    'image/tiff',
], ImageViewer)

ViewerRegistry.register(['application/pdf'], PDFViewer)

ViewerRegistry.register([
    'video/mp4',
    'video/webm',
    'video/ogg',
    'audio/mpeg',
    'audio/wav',
    'audio/ogg',
], MediaViewer)
