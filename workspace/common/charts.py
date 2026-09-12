import math
from datetime import timedelta

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


# Gantt: unlike the charts above, the SVG has a real pixel width so a long
# span scrolls instead of shrinking - the caller wraps it in overflow-x-auto.
GANTT_SCALES = ("week", "month", "quarter")
_GANTT_PX_PER_DAY = {"week": 28, "month": 9, "quarter": 3}
_GANTT_GUTTER = 200
_GANTT_HEADER = 40
_GANTT_GROUP_ROW = 30
_GANTT_ROW = 28
_GANTT_PAD_BOTTOM = 8
_GANTT_BAR_INSET = 7
_GANTT_MARKER = 6
_GANTT_LABEL_CHARS = 30
_QUARTER_START_MONTHS = (1, 4, 7, 10)


def gantt_chart(start, end, scale, groups, markers, bands, today):
    """Pre-compute the SVG geometry of a grouped gantt chart.

    *start*/*end* bound the axis (inclusive days), *scale* picks the pixel
    density and the axis labelling. *groups* hold rows that are either a
    ``bar`` (``start``..``end`` inclusive) or a ``marker`` on ``start``;
    *markers* are diamonds on the header row with a full-height guide,
    *bands* shaded ranges behind everything. Rows and bands are clamped to
    the extent; markers and a *today* outside it are dropped. A row whose
    dates fall entirely outside the extent keeps its label but draws no
    glyph: its ``x``, ``width``, ``cx`` and ``points`` come back ``None``
    rather than an empty string, so a template guard (``{% if row.width %}``)
    reads as "nothing to draw" instead of silently rendering a zero-sized
    shape. Coordinates are fixed-precision strings, see :func:`column_chart`.

    The template renders the gutter and the plot as two separate SVGs so the
    row labels stay put while only the plot scrolls; ``plot_width`` (the
    body width without the gutter) and ``today.plot_x`` (the today line's x
    relative to the plot's own viewBox) support that split. Every other
    coordinate stays relative to the full chart, gutter included.
    """
    if scale not in _GANTT_PX_PER_DAY:
        raise ValueError(f"Unknown gantt scale {scale!r}")
    px = _GANTT_PX_PER_DAY[scale]
    days = (end - start).days + 1
    width = _GANTT_GUTTER + days * px
    body_h = sum(_GANTT_GROUP_ROW + len(g["rows"]) * _GANTT_ROW for g in groups)
    height = _GANTT_HEADER + body_h + _GANTT_PAD_BOTTOM
    body_bottom = _GANTT_HEADER + body_h

    def x_of(day):
        return _GANTT_GUTTER + (day - start).days * px

    def centre_of(day):
        return x_of(day) + px / 2

    def inside(day):
        return start <= day <= end

    def clamp_range(lo, hi):
        lo, hi = max(lo, start), min(hi, end)
        if hi < lo:
            return None
        return _n(x_of(lo)), _n((hi - lo).days * px + px)

    drawn_bands = []
    for band in bands:
        span = clamp_range(band["start"], band["end"])
        if span is not None:
            drawn_bands.append(
                {
                    "x": span[0],
                    "width": span[1],
                    "label": band["label"],
                    "css_class": band["css_class"],
                }
            )

    drawn_markers = [
        _header_marker(m, centre_of, body_bottom) for m in markers if inside(m["date"])
    ]

    drawn_groups = []
    y = _GANTT_HEADER
    for group in groups:
        rows = []
        row_y = y + _GANTT_GROUP_ROW
        for row in group["rows"]:
            cy = row_y + _GANTT_ROW / 2
            drawn = {
                "id": row["id"],
                "y": _n(row_y),
                "label": _truncate(row["label"]),
                "tooltip": row["tooltip"],
                "css_class": row["css_class"],
                "kind": row["kind"],
                "x": None,
                "width": None,
                "cx": None,
                "cy": _n(cy),
                "points": None,
            }
            if row["kind"] == "bar":
                span = clamp_range(row["start"], row["end"])
                if span is not None:
                    drawn["x"], drawn["width"] = span
            elif inside(row["start"]):
                cx = centre_of(row["start"])
                drawn["cx"] = _n(cx)
                drawn["points"] = _diamond_points(cx, cy, _GANTT_MARKER / 2)
            rows.append(drawn)
            row_y += _GANTT_ROW
        drawn_groups.append(
            {
                "y": _n(y),
                "height": _n(_GANTT_GROUP_ROW),
                "label": _truncate(group["label"]),
                "sublabel": group["sublabel"],
                "progress": group["progress"],
                "rows": rows,
            }
        )
        y = row_y

    if inside(today):
        today_x = centre_of(today)
        today_geom = {"x": _n(today_x), "plot_x": _n(today_x - _GANTT_GUTTER)}
    else:
        today_geom = None
    return {
        "width": width,
        "height": height,
        "plot_width": width - _GANTT_GUTTER,
        "body_height": _n(body_h),
        "gutter": _n(_GANTT_GUTTER),
        "header": _n(_GANTT_HEADER),
        "row_height": _n(_GANTT_ROW),
        "bar_y_offset": _n(_GANTT_BAR_INSET),
        "bar_height": _n(_GANTT_ROW - 2 * _GANTT_BAR_INSET),
        "axis": _gantt_axis(start, end, scale, px, x_of),
        "bands": drawn_bands,
        "today": today_geom,
        "markers": drawn_markers,
        "groups": drawn_groups,
    }


def _header_marker(marker, centre_of, body_bottom):
    cx = centre_of(marker["date"])
    cy = _GANTT_HEADER - _GANTT_MARKER / 2 - 2
    return {
        "x": _n(cx),
        "cy": _n(cy),
        "points": _diamond_points(cx, cy, _GANTT_MARKER / 2),
        "label": marker["label"],
        "css_class": marker["css_class"],
        "guide_y1": _n(_GANTT_HEADER),
        "guide_y2": _n(body_bottom),
    }


def _diamond_points(cx, cy, half):
    """SVG polygon points for a diamond centred on (cx, cy), corner to corner."""
    return (
        f"{_n(cx)},{_n(cy - half)} {_n(cx + half)},{_n(cy)} "
        f"{_n(cx)},{_n(cy + half)} {_n(cx - half)},{_n(cy)}"
    )


def _gantt_axis(start, end, scale, px, x_of):
    """Major labels, minor ticks and weekend shading for one scale.

    The first day always gets a major label so a span starting mid-period
    is still readable; after that, week scale labels Mondays and ticks
    days, month scale labels the 1st and ticks Mondays, quarter scale
    labels quarter starts and ticks the 1st of each month.
    """
    majors, minors, weekends = [], [], []
    day = start
    while day <= end:
        first = day == start
        if scale == "week":
            minors.append({"x": _n(x_of(day))})
            if first or day.weekday() == 0:
                majors.append({"x": _n(x_of(day)), "label": day.strftime("%b %d")})
            if day.weekday() >= 5:
                weekends.append({"x": _n(x_of(day)), "width": _n(px)})
        elif scale == "month":
            if day.weekday() == 0:
                minors.append({"x": _n(x_of(day))})
            if first or day.day == 1:
                majors.append({"x": _n(x_of(day)), "label": day.strftime("%b %Y")})
        else:
            if day.day == 1:
                minors.append({"x": _n(x_of(day))})
            if first or (day.day == 1 and day.month in _QUARTER_START_MONTHS):
                quarter = (day.month - 1) // 3 + 1
                majors.append({"x": _n(x_of(day)), "label": f"Q{quarter} {day.year}"})
        day += timedelta(days=1)
    return {"majors": majors, "minors": minors, "weekends": weekends}


def _truncate(label):
    if len(label) <= _GANTT_LABEL_CHARS:
        return label
    return label[: _GANTT_LABEL_CHARS - 1] + "…"
