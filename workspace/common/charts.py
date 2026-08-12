import math

# Fixed viewBox units, not pixels: the SVG scales to fill its container.
_WIDTH = 720
_HEIGHT = 220
_PAD_LEFT = 34
_PAD_RIGHT = 6
_PAD_TOP = 8
_PAD_BOTTOM = 26
# Share of each category slot left empty, split between its two sides.
_GUTTER = 0.3


def column_chart(categories, series, *, gridlines=4):
    """Pre-compute the SVG geometry of a grouped column chart.

    *categories* are the x-axis labels; *series* is a list of
    ``{"name", "css_class", "values"}`` whose values line up with them.

    Coordinates come back as fixed-precision strings, never floats: Django
    localizes numbers in templates, so a float renders as "12,5" under a
    comma-decimal locale and silently corrupts the geometry.
    """
    plot_w = _WIDTH - _PAD_LEFT - _PAD_RIGHT
    plot_h = _HEIGHT - _PAD_TOP - _PAD_BOTTOM
    top = _axis_top(max((v for s in series for v in s["values"]), default=0), gridlines)
    slot = plot_w / len(categories) if categories else plot_w
    bar_w = slot * (1 - _GUTTER) / len(series) if series else slot
    return {
        "width": _WIDTH,
        "height": _HEIGHT,
        "plot": {
            "x": _n(_PAD_LEFT),
            "y": _n(_PAD_TOP),
            "width": _n(plot_w),
            "height": _n(plot_h),
        },
        "max": top,
        "gridlines": [
            {
                "y": _n(_PAD_TOP + plot_h - plot_h * i / gridlines),
                "value": top * i // gridlines,
            }
            for i in range(gridlines + 1)
        ],
        "categories": [
            {"x": _n(_PAD_LEFT + i * slot + slot / 2), "label": label}
            for i, label in enumerate(categories)
        ],
        "series": [{"name": s["name"], "css_class": s["css_class"]} for s in series],
        "bars": _bars(categories, series, plot_h, slot, bar_w, top),
    }


def _bars(categories, series, plot_h, slot, bar_w, top):
    bars = []
    for column, serie in enumerate(series):
        for i, value in enumerate(serie["values"]):
            height = plot_h * value / top
            bars.append(
                {
                    "x": _n(_PAD_LEFT + i * slot + slot * _GUTTER / 2 + column * bar_w),
                    "y": _n(_PAD_TOP + plot_h - height),
                    "width": _n(bar_w),
                    "height": _n(height),
                    "css_class": serie["css_class"],
                    "tooltip": f"{categories[i]}: {value} {serie['name'].lower()}",
                }
            )
    return bars


def _axis_top(raw_max, gridlines):
    """Round the axis top up so every gridline lands on a whole number."""
    if raw_max <= 0:
        return gridlines
    return math.ceil(raw_max / gridlines) * gridlines


def _n(value):
    return f"{value:.2f}"
