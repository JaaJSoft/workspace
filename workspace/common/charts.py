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


_DONUT_SIZE = 100
_DONUT_RADIUS = 40
_DONUT_STROKE = 12
# Gap between adjacent slices, in viewBox units along the ring.
_DONUT_GAP = 1.5


def donut_chart(slices):
    """Pre-compute the ring geometry of a donut chart.

    *slices* is a list of ``{"label", "value", "css_class"}``. Each slice comes
    back with a ``dasharray``/``dashoffset`` pair for a ``<circle>`` whose
    ``stroke`` is the slice colour; the template draws every slice on the same
    circle and lets the dash pattern carve out its arc. Values are formatted
    strings for the reason given in :func:`column_chart`.
    """
    circumference = 2 * math.pi * _DONUT_RADIUS
    total = sum(s["value"] for s in slices)
    arcs = []
    offset = 0.0
    for s in slices:
        share = s["value"] / total if total else 0
        length = circumference * share
        gap = min(_DONUT_GAP, length / 2) if len(slices) > 1 else 0
        arcs.append(
            {
                "label": s["label"],
                "css_class": s["css_class"],
                "dasharray": f"{_n(max(length - gap, 0))} {_n(circumference)}",
                # SVG dashes start at 3 o'clock and run clockwise; the
                # negative offset shifts each slice past the previous ones
                # and the -90° rotation in the template moves the start
                # to 12 o'clock.
                "dashoffset": _n(-(offset + gap / 2)),
            }
        )
        offset += length
    return {
        "size": _DONUT_SIZE,
        "center": _n(_DONUT_SIZE / 2),
        "radius": _n(_DONUT_RADIUS),
        "stroke": _n(_DONUT_STROKE),
        "arcs": arcs,
    }


# Widest gap the line chart may leave between two labelled x positions before
# it starts skipping labels, in viewBox units; below it adjacent labels overlap.
_MIN_LABEL_SPACING = 56
# Below this point spacing the markers merge into a beaded string that hides
# the line, so dense series (a daily cumulative flow) are drawn without them.
_MIN_MARKER_SPACING = 16


def line_chart(categories, series, *, gridlines=4, stacked=False):
    """Pre-compute the SVG geometry of a line chart, optionally stacked.

    *categories* are the x positions, *series* a list of ``{"name",
    "css_class", "values"}`` with one value per category. A ``None`` value
    is a gap: the line breaks around it and resumes at the next number,
    which is how a burndown stops at today. Optional per-series keys:
    ``fill_class`` shades the area under the line (between bands when
    stacked) and ``dashed`` draws it dotted.

    With ``stacked=True`` every series is drawn on top of the previous ones,
    so the outline of the last one is the total - the shape of a cumulative
    flow diagram. Stacked series must not contain ``None``.

    Long category lists thin their labels out to what fits and drop the
    point markers (and their tooltips) once they would touch. Values are
    formatted strings, see :func:`column_chart`.
    """
    plot_w = _WIDTH - _PAD_LEFT - _PAD_RIGHT
    plot_h = _HEIGHT - _PAD_TOP - _PAD_BOTTOM
    tops = _stacked_tops(series, len(categories)) if stacked else None
    rows = tops if stacked else [s["values"] for s in series]
    top = _axis_top(
        max((v for row in rows for v in row if v is not None), default=0), gridlines
    )
    step = plot_w / (len(categories) - 1) if len(categories) > 1 else 0

    def x_of(i):
        # A lone category sits in the middle rather than on the y axis.
        return _PAD_LEFT + (i * step if step else plot_w / 2)

    def y_of(value):
        return _PAD_TOP + plot_h - plot_h * value / top

    baseline = _PAD_TOP + plot_h
    with_markers = not step or step >= _MIN_MARKER_SPACING
    drawn = []
    for index, serie in enumerate(series):
        upper = tops[index] if stacked else serie["values"]
        lower = tops[index - 1] if stacked and index > 0 else None
        drawn.append(
            {
                "name": serie["name"],
                "css_class": serie["css_class"],
                "fill_class": serie.get("fill_class", ""),
                "dashed": bool(serie.get("dashed")),
                "segments": _segments(upper, lower, x_of, y_of, baseline),
                "markers": [
                    {
                        "x": _n(x_of(i)),
                        "y": _n(y_of(value)),
                        "tooltip": (
                            f"{categories[i]}: {_fmt(serie['values'][i])} "
                            f"{serie['name'].lower()}"
                        ),
                    }
                    for i, value in enumerate(upper)
                    if with_markers and value is not None
                ],
            }
        )
    label_every = max(1, math.ceil(_MIN_LABEL_SPACING / step)) if step else 1
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
            {"x": _n(x_of(i)), "label": label}
            for i, label in enumerate(categories)
            if i % label_every == 0
        ],
        "series": drawn,
    }


def _stacked_tops(series, length):
    """Running totals per category: row i is the top edge of series i."""
    tops = []
    running = [0] * length
    for serie in series:
        running = [
            acc + value for acc, value in zip(running, serie["values"], strict=True)
        ]
        tops.append(running)
    return tops


def _segments(upper, lower, x_of, y_of, baseline):
    """Split a series at its gaps: one polyline (and area polygon) per run
    of consecutive numbers. The area closes along *lower* when stacked,
    along the x axis otherwise."""
    segments = []
    run = []
    for i, value in enumerate([*upper, None]):
        if value is not None:
            run.append(i)
            continue
        if run:
            line = [f"{_n(x_of(i))},{_n(y_of(upper[i]))}" for i in run]
            if lower is not None:
                floor = [f"{_n(x_of(i))},{_n(y_of(lower[i]))}" for i in reversed(run)]
            else:
                floor = [
                    f"{_n(x_of(run[-1]))},{_n(baseline)}",
                    f"{_n(x_of(run[0]))},{_n(baseline)}",
                ]
            segments.append({"line": " ".join(line), "area": " ".join(line + floor)})
            run = []
    return segments


def _fmt(value):
    """Tooltip number: whole values without a trailing ".0"."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
