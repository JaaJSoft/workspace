"""
File viewers system - Server-side rendering of file viewers.

Each viewer class is responsible for generating the HTML to display
a specific type of file in the browser.
"""

from abc import ABC, abstractmethod


class ViewerRegistry:
    @classmethod
    def get_viewer(
        cls, file_type_or_mime: str, name: str = ""
    ) -> type[BaseViewer] | None:
        from workspace.files.services.filetype import get_viewer

        return get_viewer(file_type_or_mime or "", name or "")

    @classmethod
    def is_supported(cls, file_type_or_mime: str, name: str = "") -> bool:
        return cls.get_viewer(file_type_or_mime, name) is not None


class BaseViewer(ABC):
    """Base class for all file viewers."""

    handles_groups: frozenset = frozenset()
    handles_labels: frozenset = frozenset()
    weight: int = 100
    # When True, this viewer is only selected if the filename has an extension.
    # Used by fragile viewers (e.g. the Milkdown WYSIWYG editor) that should
    # not run on files merely detected by content without the user's intent.
    requires_extension: bool = False

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
        can_edit = self.can_edit() and getattr(self, "_user_can_edit", True)
        lock_info = getattr(self, "_lock_info", None)
        if lock_info:
            can_edit = False  # Force read-only when locked by another user
        content_url = (
            getattr(self, "_content_url", None)
            or f"/api/v1/files/{self.file.uuid}/content"
        )
        return {
            "file": self.file,
            "can_edit": can_edit,
            "lock_info": lock_info,
            "content_url": content_url,
        }


class TextViewer(BaseViewer):
    """Viewer for text and code files with Monaco Editor."""

    handles_groups = frozenset({"code", "text"})
    weight = 100

    def render(self, request) -> str:
        """Render Monaco Editor for text files."""
        from django.template.loader import render_to_string

        # Read file content
        file_handle = None
        try:
            file_handle = self.file.content.open("rb")
            content = file_handle.read().decode("utf-8")
        except UnicodeDecodeError, AttributeError:
            content = ""
        finally:
            if file_handle:
                file_handle.close()

        context = self.get_context(request)
        context.update(
            {
                "language": self._detect_language(),
                "content": content,
            }
        )

        return render_to_string(
            "files/ui/viewers/text_viewer.html", context, request=request
        )

    def can_edit(self) -> bool:
        """Text files can be edited."""
        return True

    _LABEL_TO_MONACO = {
        "shell": "shell",
        "batch": "bat",
        "cs": "csharp",
        "cpp": "cpp",
        "h": "c",
        "hpp": "cpp",
        "objectivec": "objective-c",
        "txt": "plaintext",
        "txtascii": "plaintext",
        "txtutf8": "plaintext",
        "txtutf16": "plaintext",
        "ini": "ini",
        "toml": "ini",
        "latex": "latex",
        "rst": "restructuredtext",
        "diff": "diff",
        "dockerfile": "dockerfile",
        "makefile": "makefile",
        "cmake": "cmake",
        "powershell": "powershell",
        "proto": "protobuf",
        "hcl": "hcl",
        "verilog": "systemverilog",
        "vhdl": "vhdl",
    }

    def _detect_language(self) -> str:
        label = self.file.type or ""
        return self._LABEL_TO_MONACO.get(label, label or "plaintext")


class ImageViewer(BaseViewer):
    """Viewer for image files."""

    handles_groups = frozenset({"image"})
    weight = 100

    def get_context(self, request) -> dict:
        from django.conf import settings

        from workspace.files.models import FileFavorite

        context = super().get_context(request)
        parent_id = getattr(self.file, "parent_id", None)
        context["ai_edit_available"] = getattr(self, "_user_can_edit", True) and bool(
            getattr(settings, "AI_IMAGE_MODEL", "")
        )
        context["file_uuid"] = str(self.file.uuid)
        context["file_parent"] = str(parent_id) if parent_id else ""
        context["file_name"] = self.file.name
        context["user_can_edit"] = getattr(self, "_user_can_edit", True)
        context["is_favorite"] = (
            context["user_can_edit"]
            and FileFavorite.objects.filter(owner=request.user, file=self.file).exists()
        )
        return context

    def render(self, request) -> str:
        """Render image viewer with zoom/rotate controls."""
        from django.template.loader import render_to_string

        return render_to_string(
            "files/ui/viewers/image_viewer.html",
            self.get_context(request),
            request=request,
        )


class MarkdownViewer(BaseViewer):
    """Viewer for Markdown files with rendered preview and raw editing."""

    handles_labels = frozenset({"markdown"})
    # The Crepe WYSIWYG editor throws on content it was not authored for, so
    # only claim files whose extension confirms markdown. Content-only
    # markdown (no extension) falls back to the robust TextViewer.
    requires_extension = True
    weight = 50

    def render(self, request) -> str:
        """Render Milkdown Crepe WYSIWYG editor for Markdown files."""
        from django.template.loader import render_to_string

        file_handle = None
        try:
            file_handle = self.file.content.open("rb")
            content = file_handle.read().decode("utf-8")
        except UnicodeDecodeError, AttributeError:
            content = ""
        finally:
            if file_handle:
                file_handle.close()

        context = self.get_context(request)
        context["content"] = content

        return render_to_string(
            "files/ui/viewers/markdown_viewer.html", context, request=request
        )

    def can_edit(self) -> bool:
        return True


class PDFViewer(BaseViewer):
    """Viewer for PDF files."""

    handles_labels = frozenset({"pdf"})
    weight = 50

    def render(self, request) -> str:
        """Render PDF viewer."""
        from django.template.loader import render_to_string

        return render_to_string(
            "files/ui/viewers/pdf_viewer.html",
            self.get_context(request),
            request=request,
        )


class MediaViewer(BaseViewer):
    """Viewer for video and audio files."""

    handles_groups = frozenset({"video", "audio"})
    weight = 100

    def render(self, request) -> str:
        from django.template.loader import render_to_string

        context = self.get_context(request)
        context["is_video"] = self.file.category == "video"
        context["is_audio"] = self.file.category == "audio"

        return render_to_string(
            "files/ui/viewers/media_viewer.html", context, request=request
        )
