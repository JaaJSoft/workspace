def format_estimate(value):
    """Display string for an estimate Decimal: trailing zeros dropped ("3",
    "3.5"), empty for an unestimated task. Shared by the event snapshots and
    the task panel payload so both render the same text."""
    if value is None:
        return ""
    text = str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text
