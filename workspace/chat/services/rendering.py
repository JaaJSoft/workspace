import mistune
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

from workspace.common.services.mentions import mention_badge, substitute_mentions


class _ChatRenderer(mistune.HTMLRenderer):
    """Markdown renderer with Pygments syntax highlighting for code blocks."""

    _formatter = HtmlFormatter(nowrap=True)

    def block_code(self, code, info=None):
        lang = None
        if info:
            lang = info.strip().split()[0]
        try:
            if lang:
                lexer = get_lexer_by_name(lang)
            else:
                lexer = guess_lexer(code)
        except ClassNotFound:
            lexer = TextLexer()

        highlighted = highlight(code, lexer, self._formatter)
        lang_attr = f' data-lang="{mistune.escape(lang)}"' if lang else ""
        return f'<pre class="code-block"{lang_attr}><code>{highlighted}</code></pre>\n'

    def codespan(self, text):
        return f'<code class="code-inline">{mistune.escape(text)}</code>'

    def image(self, alt, url, title=None):
        # Strip AI-generated <img> tags — real images come through attachments.
        return f"({mistune.escape(alt)})" if alt else ""


# Markdown renderer configured for chat with syntax highlighting
_markdown = mistune.create_markdown(
    renderer=_ChatRenderer(escape=True),
    plugins=["strikethrough", "url", "table", "task_lists"],
)


_MENTION_PREFIX = "MNTN__"
_MENTION_SUFFIX = "__MNTN"


def render_message_body(body, mention_map=None):
    """Render markdown body to HTML suitable for chat messages.

    If mention_map is provided (dict of username -> user_id), @username tokens
    matching those usernames are rendered as mention badges with hover cards.
    Mentions are replaced with placeholders in raw text before markdown rendering
    to avoid corrupting URLs or code blocks.
    """
    if mention_map:
        placeholders = {}
        known = dict(mention_map)
        known.setdefault("everyone", None)

        def _placeholder(username, user_id):
            key = f"{_MENTION_PREFIX}{username}{_MENTION_SUFFIX}"
            placeholders[key] = mention_badge(username, user_id)
            return key

        body = substitute_mentions(body, known, _placeholder)
        html = _markdown(body)
        for key, badge in placeholders.items():
            html = html.replace(key, badge)
        return html

    return _markdown(body)
