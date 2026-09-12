from datetime import date

from django.test import SimpleTestCase

from workspace.common.charts import column_chart, gantt_chart, line_chart


def _series(name="Done", css_class="fill-success", values=(1, 2)):
    return [{"name": name, "css_class": css_class, "values": list(values)}]


class ColumnChartTests(SimpleTestCase):
    def test_one_bar_per_value_per_series(self):
        chart = column_chart(
            ["W1", "W2"],
            [
                {"name": "Created", "css_class": "fill-accent", "values": [1, 2]},
                {"name": "Completed", "css_class": "fill-success", "values": [3, 4]},
            ],
        )
        self.assertEqual(len(chart["bars"]), 4)

    def test_axis_top_is_rounded_up_to_a_whole_gridline_step(self):
        # Raw max 5 over 4 gridlines rounds the step up to 2, so the axis
        # tops out at 8 and every tick label is a whole number.
        chart = column_chart(["W1"], _series(values=(5,)))
        self.assertEqual(chart["max"], 8)
        self.assertEqual([g["value"] for g in chart["gridlines"]], [0, 2, 4, 6, 8])

    def test_all_zero_series_does_not_divide_by_zero(self):
        chart = column_chart(["W1", "W2"], _series(values=(0, 0)))
        self.assertEqual(chart["max"], 4)
        self.assertEqual([bar["height"] for bar in chart["bars"]], ["0.00", "0.00"])

    def test_tallest_bar_fills_the_plot_area(self):
        chart = column_chart(["W1"], _series(values=(4,)))
        self.assertEqual(chart["bars"][0]["height"], chart["plot"]["height"])
        self.assertEqual(chart["bars"][0]["y"], chart["plot"]["y"])

    def test_coordinates_are_strings_so_templates_cannot_localize_them(self):
        # A raw float renders as "12,5" under a comma-decimal locale, which
        # silently corrupts the SVG geometry.
        chart = column_chart(["W1"], _series(values=(3,)))
        bar = chart["bars"][0]
        for key in ("x", "y", "width", "height"):
            self.assertIsInstance(bar[key], str)
        self.assertIsInstance(chart["max"], int)
        self.assertIsInstance(chart["gridlines"][0]["value"], int)

    def test_bars_of_a_series_share_a_width_and_advance_left_to_right(self):
        chart = column_chart(["W1", "W2", "W3"], _series(values=(1, 1, 1)))
        widths = {bar["width"] for bar in chart["bars"]}
        self.assertEqual(len(widths), 1)
        xs = [float(bar["x"]) for bar in chart["bars"]]
        self.assertEqual(xs, sorted(xs))

    def test_series_share_a_slot_without_overlapping(self):
        chart = column_chart(
            ["W1"],
            [
                {"name": "Created", "css_class": "fill-accent", "values": [1]},
                {"name": "Completed", "css_class": "fill-success", "values": [1]},
            ],
        )
        first, second = chart["bars"]
        self.assertLessEqual(
            float(first["x"]) + float(first["width"]), float(second["x"]) + 0.01
        )

    def test_category_labels_are_centred_on_their_slot(self):
        chart = column_chart(["W1", "W2"], _series(values=(1, 1)))
        first, second = chart["categories"]
        self.assertEqual(first["label"], "W1")
        self.assertLess(float(first["x"]), float(second["x"]))

    def test_empty_input_renders_an_empty_chart_without_raising(self):
        chart = column_chart([], [])
        self.assertEqual(chart["bars"], [])
        self.assertEqual(chart["categories"], [])
        self.assertEqual(chart["series"], [])
        self.assertEqual(chart["max"], 4)

    def test_legend_carries_each_series_name_and_colour(self):
        chart = column_chart(
            ["W1"],
            [
                {"name": "Created", "css_class": "fill-accent", "values": [1]},
                {"name": "Completed", "css_class": "fill-success", "values": [1]},
            ],
        )
        self.assertEqual(
            chart["series"],
            [
                {"name": "Created", "css_class": "fill-accent"},
                {"name": "Completed", "css_class": "fill-success"},
            ],
        )

    def test_bar_tooltip_names_its_category_and_series(self):
        chart = column_chart(["Mar 03"], _series(name="Completed", values=(7,)))
        self.assertEqual(chart["bars"][0]["tooltip"], "Mar 03: 7 completed")


class DonutChartTests(SimpleTestCase):
    def test_arcs_cover_the_ring_in_order(self):
        from workspace.common.charts import donut_chart

        chart = donut_chart(
            [
                {"label": "A", "value": 3, "css_class": "text-primary"},
                {"label": "B", "value": 1, "css_class": "text-secondary"},
            ]
        )
        self.assertEqual([a["label"] for a in chart["arcs"]], ["A", "B"])
        circumference = float(chart["arcs"][0]["dasharray"].split()[1])
        lengths = [float(a["dasharray"].split()[0]) for a in chart["arcs"]]
        # Each slice is its share of the ring minus the inter-slice gap.
        self.assertAlmostEqual(lengths[0], circumference * 0.75 - 1.5, places=1)
        self.assertAlmostEqual(lengths[1], circumference * 0.25 - 1.5, places=1)
        # The second slice starts where the first one ends.
        self.assertAlmostEqual(
            -float(chart["arcs"][1]["dashoffset"]),
            circumference * 0.75 + 0.75,
            places=1,
        )

    def test_single_slice_has_no_gap_and_zero_total_draws_nothing(self):
        from workspace.common.charts import donut_chart

        one = donut_chart([{"label": "A", "value": 5, "css_class": "x"}])
        length, circumference = one["arcs"][0]["dasharray"].split()
        self.assertEqual(length, circumference)
        empty = donut_chart([{"label": "A", "value": 0, "css_class": "x"}])
        self.assertEqual(empty["arcs"][0]["dasharray"].split()[0], "0.00")


def _line(name="Remaining", css_class="stroke-accent", values=(4, 2, 0), **extra):
    return {"name": name, "css_class": css_class, "values": list(values), **extra}


class LineChartTests(SimpleTestCase):
    def test_one_marker_per_value_and_a_single_segment_without_gaps(self):
        chart = line_chart(["D1", "D2", "D3"], [_line()])
        serie = chart["series"][0]
        self.assertEqual(len(serie["markers"]), 3)
        self.assertEqual(len(serie["segments"]), 1)
        self.assertEqual(serie["segments"][0]["line"].count(","), 3)

    def test_a_none_value_breaks_the_line_and_drops_its_marker(self):
        chart = line_chart(["D1", "D2", "D3", "D4"], [_line(values=(4, None, 2, 1))])
        serie = chart["series"][0]
        self.assertEqual(len(serie["segments"]), 2)
        self.assertEqual(len(serie["markers"]), 3)

    def test_trailing_nones_stop_the_line_where_the_data_ends(self):
        chart = line_chart(["D1", "D2", "D3"], [_line(values=(4, 2, None))])
        serie = chart["series"][0]
        self.assertEqual(len(serie["segments"]), 1)
        self.assertEqual(len(serie["markers"]), 2)

    def test_first_and_last_points_span_the_plot_width(self):
        chart = line_chart(["D1", "D2", "D3"], [_line()])
        xs = [float(m["x"]) for m in chart["series"][0]["markers"]]
        self.assertEqual(xs[0], float(chart["plot"]["x"]))
        self.assertAlmostEqual(
            xs[-1], float(chart["plot"]["x"]) + float(chart["plot"]["width"]), places=2
        )

    def test_highest_value_touches_the_top_of_the_plot(self):
        chart = line_chart(["D1", "D2"], [_line(values=(4, 1))])
        self.assertEqual(chart["series"][0]["markers"][0]["y"], chart["plot"]["y"])

    def test_area_polygon_closes_along_the_x_axis(self):
        chart = line_chart(["D1", "D2"], [_line(values=(4, 2), fill_class="fill-info")])
        area = chart["series"][0]["segments"][0]["area"].split()
        self.assertEqual(len(area), 4)
        baseline = float(chart["plot"]["y"]) + float(chart["plot"]["height"])
        self.assertEqual({float(p.split(",")[1]) for p in area[2:]}, {baseline})

    def test_stacked_series_sit_on_top_of_each_other(self):
        chart = line_chart(
            ["D1", "D2"],
            [_line("Backlog", values=(1, 1)), _line("Active", values=(2, 2))],
            stacked=True,
        )
        # The axis covers the stacked total, not the tallest single series.
        self.assertEqual(chart["max"], 4)
        backlog_y = float(chart["series"][0]["markers"][0]["y"])
        active_y = float(chart["series"][1]["markers"][0]["y"])
        self.assertLess(active_y, backlog_y)
        # The upper band's area closes along the lower band's line, so the
        # two polygons never overlap.
        area = chart["series"][1]["segments"][0]["area"].split()
        self.assertEqual(float(area[-1].split(",")[1]), backlog_y)

    def test_stacked_tooltips_report_the_series_own_value_not_the_total(self):
        chart = line_chart(
            ["D1"],
            [_line("Backlog", values=(1,)), _line("Active", values=(2,))],
            stacked=True,
        )
        self.assertEqual(chart["series"][1]["markers"][0]["tooltip"], "D1: 2 active")

    def test_whole_floats_are_printed_without_a_decimal_point(self):
        chart = line_chart(["D1", "D2"], [_line(values=(3.0, 1.5))])
        tooltips = [m["tooltip"] for m in chart["series"][0]["markers"]]
        self.assertEqual(tooltips, ["D1: 3 remaining", "D2: 1.5 remaining"])

    def test_crowded_categories_thin_their_labels_but_keep_the_first(self):
        labels = [f"D{i}" for i in range(84)]
        chart = line_chart(labels, [_line(values=[1] * 84)])
        shown = [c["label"] for c in chart["categories"]]
        self.assertLess(len(shown), 20)
        self.assertEqual(shown[0], "D0")

    def test_dense_series_are_drawn_without_markers(self):
        chart = line_chart([f"D{i}" for i in range(84)], [_line(values=[1] * 84)])
        self.assertEqual(chart["series"][0]["markers"], [])
        self.assertEqual(len(chart["series"][0]["segments"]), 1)

    def test_a_sprint_long_series_keeps_its_markers(self):
        chart = line_chart([f"D{i}" for i in range(30)], [_line(values=[1] * 30)])
        self.assertEqual(len(chart["series"][0]["markers"]), 30)

    def test_dashed_flag_is_passed_through(self):
        chart = line_chart(["D1"], [_line(values=(1,), dashed=True)])
        self.assertTrue(chart["series"][0]["dashed"])
        self.assertFalse(
            line_chart(["D1"], [_line(values=(1,))])["series"][0]["dashed"]
        )

    def test_coordinates_are_strings_so_templates_cannot_localize_them(self):
        chart = line_chart(["D1", "D2"], [_line(values=(1, 2))])
        marker = chart["series"][0]["markers"][0]
        self.assertIsInstance(marker["x"], str)
        self.assertIsInstance(marker["y"], str)
        self.assertNotIn(
            ".", chart["series"][0]["segments"][0]["line"].replace(".", "", 4)
        )

    def test_empty_input_renders_an_empty_chart_without_raising(self):
        chart = line_chart([], [])
        self.assertEqual(chart["series"], [])
        self.assertEqual(chart["categories"], [])
        self.assertEqual(chart["max"], 4)

    def test_a_single_category_is_drawn_without_dividing_by_zero(self):
        chart = line_chart(["D1"], [_line(values=(2,))])
        self.assertEqual(len(chart["series"][0]["markers"]), 1)
        self.assertEqual(chart["categories"][0]["label"], "D1")


def _gantt(**overrides):
    kwargs = {
        "start": date(2026, 9, 7),
        "end": date(2026, 9, 20),
        "scale": "week",
        "groups": [
            {
                "label": "Beta",
                "sublabel": "Oct 1",
                "progress": "1/2",
                "rows": [
                    {
                        "id": "t1",
                        "label": "WR-1 Build",
                        "start": date(2026, 9, 8),
                        "end": date(2026, 9, 10),
                        "kind": "bar",
                        "css_class": "fill-accent",
                        "tooltip": "WR-1 Build",
                    },
                    {
                        "id": "t2",
                        "label": "WR-2 Ship",
                        "start": date(2026, 9, 15),
                        "end": None,
                        "kind": "marker",
                        "css_class": "fill-success",
                        "tooltip": "WR-2 Ship",
                    },
                ],
            }
        ],
        "markers": [
            {"label": "Beta", "date": date(2026, 9, 18), "css_class": "fill-warning"}
        ],
        "bands": [
            {
                "label": "Sprint 1",
                "start": date(2026, 9, 7),
                "end": date(2026, 9, 13),
                "css_class": "fill-info/10",
            }
        ],
        "today": date(2026, 9, 9),
    }
    kwargs.update(overrides)
    return gantt_chart(**kwargs)


class GanttChartTests(SimpleTestCase):
    def test_width_grows_with_the_span_and_the_scale(self):
        week = _gantt()
        quarter = _gantt(scale="quarter")
        self.assertEqual(week["width"], int(float(week["gutter"])) + 14 * 28)
        self.assertEqual(quarter["width"], int(float(quarter["gutter"])) + 14 * 3)

    def test_height_counts_the_header_the_group_row_and_each_task_row(self):
        chart = _gantt()
        expected = 40 + 30 + 2 * 28 + 8
        self.assertEqual(chart["height"], expected)

    def test_bar_spans_its_days_inclusive(self):
        row = _gantt()["groups"][0]["rows"][0]
        gutter = float(_gantt()["gutter"])
        self.assertEqual(float(row["x"]), gutter + 1 * 28)
        self.assertEqual(float(row["width"]), 3 * 28)
        self.assertEqual(row["kind"], "bar")

    def test_marker_sits_in_the_middle_of_its_day(self):
        row = _gantt()["groups"][0]["rows"][1]
        gutter = float(_gantt()["gutter"])
        self.assertEqual(float(row["cx"]), gutter + 8 * 28 + 14)
        self.assertEqual(row["kind"], "marker")

    def test_row_marker_diamond_is_centred_on_cx_and_cy(self):
        row = _gantt()["groups"][0]["rows"][1]
        cx, cy = float(row["cx"]), float(row["cy"])
        points = [tuple(map(float, p.split(","))) for p in row["points"].split()]
        xs, ys = zip(*points, strict=True)
        self.assertEqual((min(xs), max(xs)), (cx - 3, cx + 3))
        self.assertEqual((min(ys), max(ys)), (cy - 3, cy + 3))

    def test_row_entirely_outside_the_extent_keeps_its_label_but_draws_nothing(self):
        chart = _gantt(
            groups=[
                {
                    "label": "g",
                    "sublabel": "",
                    "progress": "",
                    "rows": [
                        {
                            "id": "t",
                            "label": "gone",
                            "start": date(2027, 1, 1),
                            "end": date(2027, 1, 2),
                            "kind": "bar",
                            "css_class": "fill-accent",
                            "tooltip": "gone",
                        }
                    ],
                }
            ]
        )
        row = chart["groups"][0]["rows"][0]
        self.assertEqual(row["label"], "gone")
        self.assertIsNone(row["x"])
        self.assertIsNone(row["width"])
        self.assertIsNone(row["cx"])
        self.assertIsNone(row["points"])

    def test_bar_is_clamped_to_the_extent(self):
        chart = _gantt(
            groups=[
                {
                    "label": "g",
                    "sublabel": "",
                    "progress": "",
                    "rows": [
                        {
                            "id": "t",
                            "label": "l",
                            "start": date(2026, 9, 1),
                            "end": date(2026, 9, 30),
                            "kind": "bar",
                            "css_class": "fill-accent",
                            "tooltip": "l",
                        }
                    ],
                }
            ]
        )
        row = chart["groups"][0]["rows"][0]
        self.assertEqual(float(row["x"]), float(chart["gutter"]))
        self.assertEqual(float(row["width"]), 14 * 28)

    def test_today_line_inside_and_outside_the_extent(self):
        inside = _gantt()
        self.assertEqual(
            float(inside["today"]["x"]), float(inside["gutter"]) + 2 * 28 + 14
        )
        self.assertIsNone(_gantt(today=date(2027, 1, 1))["today"])

    def test_milestone_marker_is_centred_on_its_day_with_a_full_height_guide(self):
        chart = _gantt()
        marker = chart["markers"][0]
        self.assertEqual(float(marker["x"]), float(chart["gutter"]) + 11 * 28 + 14)
        self.assertEqual(float(marker["guide_y2"]), chart["height"] - 8)
        self.assertEqual(marker["label"], "Beta")

    def test_header_marker_diamond_is_centred_on_its_x_and_cy(self):
        chart = _gantt()
        marker = chart["markers"][0]
        x, cy = float(marker["x"]), float(marker["cy"])
        points = [tuple(map(float, p.split(","))) for p in marker["points"].split()]
        xs, ys = zip(*points, strict=True)
        self.assertEqual((min(xs), max(xs)), (x - 3, x + 3))
        self.assertEqual((min(ys), max(ys)), (cy - 3, cy + 3))

    def test_marker_outside_the_extent_is_dropped(self):
        chart = _gantt(
            markers=[{"label": "Far", "date": date(2027, 1, 1), "css_class": "x"}]
        )
        self.assertEqual(chart["markers"], [])

    def test_band_covers_its_days_and_is_clamped(self):
        chart = _gantt(
            bands=[
                {
                    "label": "S",
                    "start": date(2026, 9, 1),
                    "end": date(2026, 9, 13),
                    "css_class": "fill-info/10",
                }
            ]
        )
        band = chart["bands"][0]
        self.assertEqual(float(band["x"]), float(chart["gutter"]))
        self.assertEqual(float(band["width"]), 7 * 28)

    def test_week_scale_labels_every_monday_and_ticks_every_day(self):
        axis = _gantt()["axis"]
        self.assertEqual([m["label"] for m in axis["majors"]], ["Sep 07", "Sep 14"])
        self.assertEqual(len(axis["minors"]), 14)
        self.assertEqual(len(axis["weekends"]), 4)

    def test_month_scale_labels_months_and_ticks_mondays(self):
        axis = _gantt(scale="month", start=date(2026, 9, 1), end=date(2026, 10, 31))[
            "axis"
        ]
        self.assertEqual([m["label"] for m in axis["majors"]], ["Sep 2026", "Oct 2026"])
        self.assertEqual(len(axis["minors"]), 8)
        self.assertEqual(axis["weekends"], [])

    def test_quarter_scale_labels_quarters_and_ticks_months(self):
        axis = _gantt(scale="quarter", start=date(2026, 9, 1), end=date(2027, 1, 31))[
            "axis"
        ]
        self.assertEqual(
            [m["label"] for m in axis["majors"]], ["Q3 2026", "Q4 2026", "Q1 2027"]
        )
        self.assertEqual(len(axis["minors"]), 5)

    def test_first_partial_period_still_gets_a_major_label(self):
        axis = _gantt(start=date(2026, 9, 9), end=date(2026, 9, 20))["axis"]
        self.assertEqual(axis["majors"][0]["label"], "Sep 09")

    def test_long_labels_are_truncated_with_an_ellipsis(self):
        chart = _gantt(
            groups=[
                {
                    "label": "g",
                    "sublabel": "",
                    "progress": "",
                    "rows": [
                        {
                            "id": "t",
                            "label": "x" * 60,
                            "start": date(2026, 9, 8),
                            "end": None,
                            "kind": "marker",
                            "css_class": "fill-accent",
                            "tooltip": "long",
                        }
                    ],
                }
            ]
        )
        label = chart["groups"][0]["rows"][0]["label"]
        self.assertEqual(len(label), 30)
        self.assertTrue(label.endswith("…"))

    def test_unknown_scale_raises(self):
        with self.assertRaises(ValueError):
            _gantt(scale="decade")

    def test_coordinates_are_strings_so_templates_cannot_localize_them(self):
        chart = _gantt()
        row = chart["groups"][0]["rows"][0]
        for value in (
            row["x"],
            row["y"],
            row["width"],
            chart["today"]["x"],
            chart["gutter"],
        ):
            self.assertIsInstance(value, str)
            self.assertNotIn(",", value)
