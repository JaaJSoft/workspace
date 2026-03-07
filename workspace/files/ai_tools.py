"""AI tools for the Files module."""
import base64
import json

from workspace.ai.tool_registry import Param, ToolProvider, tool


class FilesToolProvider(ToolProvider):

    @tool(badge_icon='📄', badge_label='Read file', detail_key='uuid', params={
        'uuid': Param('The UUID of the file to read.'),
    })
    def read_file(self, args, user, bot, conversation_id):
        """Read the content of a file by its UUID. Works for text files and images. \
Use this when the user asks to read, open, view, or see the contents of a specific file, \
typically after finding it via search_workspace."""
        file_uuid = args.get('uuid', '').strip()
        if not file_uuid:
            return 'Error: uuid is required'
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
