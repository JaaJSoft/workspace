"""AI tools for the Files module."""
import base64
import json

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool


class ReadFileParams(BaseModel):
    uuid: str = Field(description="The UUID of the file to read.")


class SearchFilesParams(BaseModel):
    query: str = Field(description="The search term to look for in file names.")
    file_type: str = Field(default="", description="Filter by type: file or folder.")


class FilesToolProvider(ToolProvider):

    @tool(badge_icon='📄', badge_label='Read file', detail_key='uuid', params=ReadFileParams)
    def read_file(self, args, user, bot, conversation_id, context):
        """Read the content of a file by its UUID. Supports text files (returns text) and images (returns the image). \
Call this after finding a file via search_files to get its content, \
or when the user asks to read, open, view, or see a specific file."""
        import uuid as uuid_mod
        file_uuid = args.uuid.strip()
        if not file_uuid:
            return 'Error: uuid is required'
        try:
            uuid_mod.UUID(file_uuid)
        except ValueError:
            return 'Error: invalid UUID format.'
        from workspace.files.models import File
        from workspace.files.services import FileService
        file_obj = File.objects.filter(uuid=file_uuid).select_related('owner').first()
        if not file_obj or not FileService.can_access(user, file_obj):
            return 'File not found or access denied.'
        if file_obj.node_type != File.NodeType.FILE:
            return 'This is a folder, not a file.'
        # Try image first
        raw, mime = FileService.read_image_content(file_obj)
        if raw is not None:
            return json.dumps({
                'type': 'image',
                'mime_type': mime,
                'data': base64.b64encode(raw).decode(),
            })
        # Fall back to text
        text = FileService.read_text_content(file_obj, max_bytes=32_768)
        if text is None:
            return f'Cannot read "{file_obj.name}" — unsupported file type.'
        header = f'File: {file_obj.name}'
        if file_obj.mime_type:
            header += f' ({file_obj.mime_type})'
        return f'{header}\n\n{text}'

    @tool(badge_icon='🔍', badge_label='Searched files', detail_key='query', params=SearchFilesParams)
    def search_files(self, args, user, bot, conversation_id, context):
        """Search through your files and folders by name. \
Returns up to 20 matches with name, type, and parent folder. \
Call this when the user asks to find, look up, or locate a file or folder. \
Use read_file with the returned UUID to get the content."""
        query = args.query.strip()
        if not query:
            return 'Error: query is required'

        from workspace.files.services import FileService

        qs = FileService.user_files_qs(user).filter(
            name__icontains=query,
        ).select_related('parent').order_by('-updated_at')

        file_type = args.file_type.strip().lower()
        if file_type == 'file':
            qs = qs.filter(node_type=File.NodeType.FILE)
        elif file_type == 'folder':
            qs = qs.filter(node_type=File.NodeType.FOLDER)

        matches = qs[:20]
        if not matches:
            return f'No files found matching "{query}".'

        results = []
        for f in matches:
            results.append({
                'uuid': str(f.uuid),
                'name': f.name,
                'type': f.node_type,
                'mime_type': f.mime_type or '',
                'parent_folder': f.parent.name if f.parent else '',
                'updated_at': f.updated_at.strftime('%Y-%m-%d %H:%M'),
            })
        return json.dumps(results, ensure_ascii=False)
