import mistune

# escape=True: raw HTML in the description is escaped, so user input can
# never inject markup into the panel.
_markdown = mistune.create_markdown(
    escape=True,
    plugins=["strikethrough", "url", "table", "task_lists"],
)


def render_task_description(text):
    """Render a task description (markdown) to safe HTML."""
    if not text:
        return ""
    return _markdown(text)
