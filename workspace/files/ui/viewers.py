"""
File viewers system - Server-side rendering of file viewers.

Each viewer class is responsible for generating the HTML to display
a specific type of file in the browser.
"""

from abc import ABC, abstractmethod

# Stable id the viewer panel hosts (files viewer modal, chat attachment
# viewer modal, notes editor pane) target with alpine-ajax. The client keeps an
# element with this id mounted at all times; every viewer response replaces it.
VIEWER_PANEL_ID = "viewer-panel"


def render_viewer_panel(html: str) -> str:
    """Wrap viewer markup in the id-bearing element alpine-ajax merges by.

    ``display: contents`` keeps the wrapper out of layout, so the host
    container's flex/overflow semantics apply to the viewer markup directly -
    the wrapper exists only as a merge anchor.
    """
    return f'<div id="{VIEWER_PANEL_ID}" style="display: contents">{html}</div>'


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
    # Stable identifier persisted by the `viewer` override columns. Survives a
    # class rename, unlike __name__.
    slug: str = ""

    def __init__(self, file_obj):
        """
        Initialize viewer with file object.

        Args:
            file_obj: File model instance
        """
        self.file = file_obj

    @classmethod
    def is_enabled(cls) -> bool:
        """Whether this viewer may claim files right now.

        Checked on every resolution, so a viewer gated on deployment state
        (e.g. the WOPI editor being configured) turns whole file types
        viewable or download-only without any per-file migration. Must stay
        cheap: it runs once per viewer class per file in folder listings.
        """
        return True

    @classmethod
    def claimed_labels(cls) -> frozenset:
        """Labels this viewer claims right now.

        Defaults to the static ``handles_labels``; viewers whose coverage
        depends on deployment state (the WOPI editor's advertised formats)
        override this. Same cheapness constraint as ``is_enabled``.
        """
        return cls.handles_labels

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
    slug = "text"

    def render(self, request) -> str:
        """Render Monaco Editor for text files."""
        from django.template.loader import render_to_string

        file_handle = None
        try:
            file_handle = self.file.content.open("rb")
            content = file_handle.read().decode("utf-8")
        # An empty field and a vanished blob both mean "nothing to show" -
        # render an empty editor rather than failing the page.
        except UnicodeDecodeError, AttributeError, ValueError, FileNotFoundError:
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
    slug = "image"

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
    slug = "markdown"

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
    slug = "pdf"

    def render(self, request) -> str:
        """Render PDF viewer."""
        from django.template.loader import render_to_string

        return render_to_string(
            "files/ui/viewers/pdf_viewer.html",
            self.get_context(request),
            request=request,
        )


def _render_media(viewer, request, *, is_audio: bool) -> str:
    """Shared body for the audio and video viewers.

    A module function rather than a shared base class on purpose: viewer
    resolution walks BaseViewer.__subclasses__(), which only yields direct
    subclasses, so an intermediate class would drop both viewers from the
    registry without any error.
    """
    from django.template.loader import render_to_string

    context = viewer.get_context(request)
    context["is_audio"] = is_audio
    context["is_video"] = not is_audio
    return render_to_string(
        "files/ui/viewers/media_viewer.html", context, request=request
    )


class AudioViewer(BaseViewer):
    """Viewer for audio files."""

    handles_groups = frozenset({"audio"})
    weight = 100
    slug = "audio"

    def render(self, request) -> str:
        return _render_media(self, request, is_audio=True)


class VideoViewer(BaseViewer):
    """Viewer for video files."""

    handles_groups = frozenset({"video"})
    weight = 100
    slug = "video"

    def render(self, request) -> str:
        return _render_media(self, request, is_audio=False)


class OfficeViewer(BaseViewer):
    """Office documents rendered by the deployer's WOPI editor.

    The iframe URL and the supported actions come from the editor's discovery
    XML; this class only decides view vs edit from the user's permission and
    hands the editor a signed access token. With no WOPI editor configured the
    viewer is disabled and office files stay download-only.
    """

    # Formats every WOPI editor speaks - claimed even while the discovery
    # document is unreachable. rtf is here despite Magika grouping it as
    # text: it is a word-processing format, and the text editor would show
    # its raw markup.
    handles_labels = frozenset(
        {"docx", "xlsx", "pptx", "odt", "ods", "odp", "doc", "xls", "ppt", "rtf"}
    )
    # Never claimed even when the editor advertises them: the browser's own
    # PDF renderer is lighter and needs no editor round-trip.
    _never_claimed = frozenset({"pdf"})
    weight = 50
    slug = "office"

    @classmethod
    def is_enabled(cls) -> bool:
        from django.conf import settings

        return bool(settings.WOPI_DISCOVERY_URL)

    @classmethod
    def claimed_labels(cls) -> frozenset:
        """The static core, widened by whatever the editor advertises.

        Discovery keys are extensions; Magika labels for office formats are
        their extension, so intersecting the advertised set with the KB's
        ``document`` group claims exactly the document-family formats this
        deployment's editor can open (dotx, xlsb, legacy suites, ...) while
        text-family labels the code editor owns (csv, txt) stay with it.
        """
        from workspace.files.services.detection import get_all_labels
        from workspace.files.services.wopi import discovery

        extensions = discovery.supported_extensions()
        if not extensions:
            return cls.handles_labels
        kb = get_all_labels()
        advertised_documents = {
            label
            for label in extensions
            if kb.get(label, {}).get("group") == "document"
        }
        return (cls.handles_labels | advertised_documents) - cls._never_claimed

    def can_edit(self) -> bool:
        return True

    def _extension(self) -> str:
        name = self.file.name or ""
        if "." in name:
            return name.rsplit(".", 1)[1].lower()
        return self.file.type or ""

    def render(self, request) -> str:
        from django.conf import settings
        from django.template.loader import render_to_string
        from django.urls import reverse
        from django.utils import timezone

        from workspace.files.services.wopi import discovery
        from workspace.files.services.wopi.tokens import mint_access_token

        context = self.get_context(request)
        can_edit = context["can_edit"]
        action_url = discovery.get_action_url(
            self._extension(), "edit" if can_edit else "view"
        )
        if action_url is None and can_edit:
            # The editor only publishes a view action for this format
            # (legacy types often are view-only): open read-only rather
            # than not at all.
            action_url = discovery.get_action_url(self._extension(), "view")
            can_edit = False
        if not action_url:
            return render_to_string(
                "files/ui/viewers/office_viewer_unavailable.html",
                context,
                request=request,
            )

        host = settings.WOPI_HOST_URL or request.build_absolute_uri("/").rstrip("/")
        wopi_src = host + reverse("wopi-file", kwargs={"uuid": self.file.uuid})
        context.update(
            {
                "editor_url": discovery.build_editor_url(action_url, wopi_src),
                "access_token": mint_access_token(
                    request.user, self.file.uuid, can_edit
                ),
                # Absolute expiry in milliseconds since epoch, per the WOPI
                # token contract.
                "access_token_ttl": int(
                    (timezone.now().timestamp() + settings.WOPI_TOKEN_TTL) * 1000
                ),
            }
        )
        return render_to_string(
            "files/ui/viewers/office_viewer.html", context, request=request
        )
