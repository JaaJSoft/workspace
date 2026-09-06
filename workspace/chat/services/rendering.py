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


# Markdown renderer configured for chat with syntax highlighting. hard_wrap is
# what makes a Shift+Enter survive: markdown's soft break would collapse it into
# a space, which nobody expects from a chat composer.
_markdown = mistune.create_markdown(
    renderer=_ChatRenderer(escape=True),
    plugins=["strikethrough", "url", "table", "task_lists"],
    hard_wrap=True,
)


_MENTION_PREFIX = "MNTN__"
_MENTION_SUFFIX = "__MNTN"


def render_message_body(body, mention_map=None, *, allow_everyone=True):
    """Render markdown body to HTML suitable for chat messages.

    If mention_map is provided (dict of username -> user_id), @username tokens
    matching those usernames are rendered as mention badges with hover cards.
    Mentions are replaced with placeholders in raw text before markdown rendering
    to avoid corrupting URLs or code blocks.

    ``@everyone`` is otherwise always renderable, whether or not the caller
    put it in the map. *allow_everyone=False* is for a caller that will not
    notify anyone - the guest message path - where the badge would promise a
    ping nobody receives; it is refused here rather than at the map, because
    the map alone cannot express it (a body naming a real member keeps the
    map non-empty, and the default below would put "everyone" back).
    """
    if mention_map:
        placeholders = {}
        known = dict(mention_map)
        if allow_everyone:
            known.setdefault("everyone", None)
        else:
            known.pop("everyone", None)

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
