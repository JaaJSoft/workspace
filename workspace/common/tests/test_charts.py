from django.test import SimpleTestCase

from workspace.common.charts import column_chart, line_chart


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
