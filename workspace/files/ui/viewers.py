"""
File viewers system - Server-side rendering of file viewers.

Each viewer class is responsible for generating the HTML to display
a specific type of file in the browser.
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional, Type


class ViewerRegistry:
    """Registry for file viewers based on MIME types."""

    _VIEWER_TYPE_MAP: Dict[str, Type['BaseViewer']] = {}

    @classmethod
    def _ensure_map(cls):
        if not cls._VIEWER_TYPE_MAP:
            cls._VIEWER_TYPE_MAP = {
                'text': TextViewer,
                'image': ImageViewer,
                'markdown': MarkdownViewer,
                'pdf': PDFViewer,
                'media': MediaViewer,
            }

    @classmethod
    def get_viewer(cls, mime_type: str) -> Optional[Type['BaseViewer']]:
        """Get appropriate viewer for a MIME type."""
        from workspace.files.services.mime import get_viewer_type

        vt = get_viewer_type(mime_type)
        if vt is None:
            return None
        cls._ensure_map()
        return cls._VIEWER_TYPE_MAP.get(vt)

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
        can_edit = self.can_edit() and getattr(self, '_user_can_edit', True)
        return {
            'file': self.file,
            'can_edit': can_edit,
        }


class TextViewer(BaseViewer):
    """Viewer for text and code files with Monaco Editor."""

    def render(self, request) -> str:
        """Render Monaco Editor for text files."""
        from django.template.loader import render_to_string

        # Read file content
        file_handle = None
        try:
            file_handle = self.file.content.open('rb')
            content = file_handle.read().decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            content = ''
        finally:
            if file_handle:
                file_handle.close()

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
        # Handle files with no extension by name
        name_lower = self.file.name.lower()
        name_map = {
            'dockerfile': 'dockerfile',
            'makefile': 'makefile',
            'gemfile': 'ruby',
            'rakefile': 'ruby',
        }
        if '.' not in self.file.name and name_lower in name_map:
            return name_map[name_lower]

        ext = self.file.name.split('.')[-1].lower() if '.' in self.file.name else ''

        lang_map = {
            'js': 'javascript',
            'jsx': 'javascript',
            'ts': 'typescript',
            'tsx': 'typescript',
            'py': 'python',
            'pyw': 'python',
            'html': 'html',
            'htm': 'html',
            'css': 'css',
            'scss': 'scss',
            'less': 'less',
            'json': 'json',
            'jsonc': 'json',
            'md': 'markdown',
            'xml': 'xml',
            'svg': 'xml',
            'yaml': 'yaml',
            'yml': 'yaml',
            'sh': 'shell',
            'bash': 'shell',
            'zsh': 'shell',
            'sql': 'sql',
            'php': 'php',
            'java': 'java',
            'c': 'c',
            'h': 'c',
            'cpp': 'cpp',
            'cxx': 'cpp',
            'cc': 'cpp',
            'hpp': 'cpp',
            'cs': 'csharp',
            'go': 'go',
            'rs': 'rust',
            'rb': 'ruby',
            'lua': 'lua',
            'swift': 'swift',
            'kt': 'kotlin',
            'kts': 'kotlin',
            'r': 'r',
            'R': 'r',
            'pl': 'perl',
            'pm': 'perl',
            'ini': 'ini',
            'toml': 'ini',
            'cfg': 'ini',
            'dockerfile': 'dockerfile',
            'ps1': 'powershell',
            'psm1': 'powershell',
            'bat': 'bat',
            'cmd': 'bat',
            'graphql': 'graphql',
            'gql': 'graphql',
            'proto': 'protobuf',
            'tf': 'hcl',
        }

        return lang_map.get(ext, 'plaintext')


class ImageViewer(BaseViewer):
    """Viewer for image files."""

    def render(self, request) -> str:
        """Render image viewer with zoom/rotate controls."""
        from django.template.loader import render_to_string

        return render_to_string('files/ui/viewers/image_viewer.html', self.get_context(request), request=request)


class MarkdownViewer(BaseViewer):
    """Viewer for Markdown files with rendered preview and raw editing."""

    def render(self, request) -> str:
        """Render Milkdown Crepe WYSIWYG editor for Markdown files."""
        from django.template.loader import render_to_string

        file_handle = None
        try:
            file_handle = self.file.content.open('rb')
            content = file_handle.read().decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            content = ''
        finally:
            if file_handle:
                file_handle.close()

        context = self.get_context(request)
        context['content'] = content

        return render_to_string('files/ui/viewers/markdown_viewer.html', context, request=request)

    def can_edit(self) -> bool:
        return True


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


